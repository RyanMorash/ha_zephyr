#!/usr/bin/env python3
"""
Read AWS IoT device shadow for Gemtek/Zephyr range hoods.

  pip install boto3 pycognito requests awsiotsdk

Chain:
  1. Cognito User Pool (SRP)        -> ID token
  2. Cognito Identity Pool          -> temporary AWS credentials
  3. iot:AttachPolicy               -> bind RangeHoodPolicy to this identity
  4. vendor API (getowndevices)     -> thing name(s)
  5. MQTT over WebSocket            -> shadow get + live updates

Step 3 is the one that is easy to miss: without an IoT policy attached to
the Cognito identity, CONNECT / SUBSCRIBE / PUBLISH all succeed and every
message is silently dropped at delivery.

Read-only by design. Nothing here publishes to shadow/update.

  export ZEPHYR_USER=you@example.com
  python zephyr_shadow.py                  # one-shot shadow read
  python zephyr_shadow.py --watch          # then stream updates
  python zephyr_shadow.py --show-policy    # dump the IoT policy document
"""

import argparse
import getpass
import json
import os
import sys
import threading
import time

import boto3
import requests
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from pycognito import Cognito

from awscrt import auth, mqtt
from awsiot import mqtt_connection_builder

# --- config ---------------------------------------------------------
REGION    = "us-west-2"
USER_POOL = "us-west-2_McuoKpkna"
CLIENT_ID = "5a2qiskdvvu7gre1jvbjnunu20"
SECRET    = "3b085l2fkgph4kt734k5e26tirb9hjasgb4rn8sjpp4mheo5kga"
ID_POOL   = "us-west-2:fb4c1b66-12c2-414b-83a1-a1902f7d98e3"
ENDPOINT  = "a1nqxu0hki9zw3-ats.iot.us-west-2.amazonaws.com"
PROVIDER  = f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL}"
POLICY    = "RangeHoodPolicy"

DEVICE_API = "https://zephyr-prod-app.gemteks.com/prod/getowndevices"
CA_BUNDLE  = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "gemtek-chain.pem")

USERNAME = os.environ.get("ZEPHYR_USER", "")
PASSWORD = os.environ.get("ZEPHYR_PASS", "")


# --- session --------------------------------------------------------
class Session:
    """Holds the Cognito user + current AWS credentials, and can refresh."""

    def __init__(self, username, password):
        self.user = Cognito(USER_POOL, CLIENT_ID, client_secret=SECRET,
                            username=username, user_pool_region=REGION)
        self.user.authenticate(password=password)
        print("[+] user pool auth ok")
        self.identity_id = None
        self.creds = None
        self._exchange()

    def _exchange(self):
        ci = boto3.client("cognito-identity", region_name=REGION,
                          config=Config(signature_version=UNSIGNED))
        logins = {PROVIDER: self.user.id_token}
        if self.identity_id is None:
            self.identity_id = ci.get_id(IdentityPoolId=ID_POOL,
                                         Logins=logins)["IdentityId"]
        self.creds = ci.get_credentials_for_identity(
            IdentityId=self.identity_id, Logins=logins)["Credentials"]

    def refresh(self):
        """Renew the Cognito tokens and re-exchange for fresh AWS creds."""
        self.user.renew_access_token()
        self._exchange()
        print(f"[+] credentials refreshed, expire {self.creds['Expiration']}")

    @property
    def expires_at(self):
        return self.creds["Expiration"]

    def client(self, service, **kw):
        return boto3.client(
            service, region_name=REGION,
            aws_access_key_id=self.creds["AccessKeyId"],
            aws_secret_access_key=self.creds["SecretKey"],
            aws_session_token=self.creds["SessionToken"],
            **kw)


# --- IoT policy attachment (the missing step) ------------------------
def attach_policy(sess, policy=POLICY):
    """Bind the IoT policy to this Cognito identity.

    The vendor app does this on every launch (PUT /target-policies/<name>).
    Idempotent. Must happen BEFORE connecting - an existing MQTT session
    does not pick up newly attached permissions.
    """
    iot = sess.client("iot")
    try:
        attached = iot.list_attached_policies(target=sess.identity_id)
        names = [p["policyName"] for p in attached.get("policies", [])]
        if policy in names:
            print(f"[+] {policy} already attached")
            return True
        print(f"[*] currently attached: {names or 'none'}")
    except ClientError as e:
        print(f"[-] list_attached_policies: {e.response['Error']['Code']}")

    try:
        iot.attach_policy(policyName=policy, target=sess.identity_id)
        print(f"[+] attached {policy} -> {sess.identity_id}")
        return True
    except ClientError as e:
        err = e.response["Error"]
        print(f"[-] attach_policy failed: {err['Code']}: {err.get('Message','')}",
              file=sys.stderr)
        return False


def show_policy(sess, policy=POLICY):
    """Dump the policy document, if the role is allowed to read it."""
    iot = sess.client("iot")
    try:
        doc = iot.get_policy(policyName=policy)["policyDocument"]
        print(json.dumps(json.loads(doc), indent=2))
    except ClientError as e:
        print(f"[-] get_policy: {e.response['Error']['Code']} "
              "(expected - most Cognito roles cannot read policy documents)")


# --- vendor API -> thing names ---------------------------------------
def list_devices(sess):
    verify = CA_BUNDLE if os.path.exists(CA_BUNDLE) else True
    r = requests.post(
        DEVICE_API,
        headers={"Authorization": sess.user.id_token,   # bare token, no Bearer
                 "Content-Type": "application/json",
                 "Accept": "application/json"},
        data=b"",                                       # empty body, per capture
        verify=verify,
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("devices", [])


# --- MQTT ------------------------------------------------------------
class ShadowClient:
    def __init__(self, sess, thing, client_suffix=""):
        self.sess = sess
        self.thing = thing
        self.base = f"$aws/things/{thing}/shadow"
        self.client_id = sess.identity_id + client_suffix
        self.conn = None
        self.got_response = threading.Event()

    def _on_msg(self, topic, payload, **kw):
        leaf = topic.rsplit("/", 1)[-1]
        print(f"\n<<< {topic}")
        try:
            print(json.dumps(json.loads(payload), indent=2))
        except Exception:
            print(repr(payload[:800]))
        if leaf in ("accepted", "rejected"):
            self.got_response.set()

    def _on_interrupt(self, connection, error, **kw):
        print(f"[-] connection interrupted: {error}")

    def _on_resume(self, connection, return_code, session_present, **kw):
        print(f"[+] resumed rc={return_code} session_present={session_present}")

    def connect(self):
        c = self.sess.creds
        self.conn = mqtt_connection_builder.websockets_with_default_aws_signing(
            endpoint=ENDPOINT, region=REGION,
            credentials_provider=auth.AwsCredentialsProvider.new_static(
                c["AccessKeyId"], c["SecretKey"], c["SessionToken"]),
            client_id=self.client_id,
            on_connection_interrupted=self._on_interrupt,
            on_connection_resumed=self._on_resume,
            clean_session=True, keep_alive_secs=30,
        )
        self.conn.connect().result(timeout=15)
        print(f"[+] mqtt connected as {self.client_id}")

    def subscribe_all(self):
        """Subscribe and report the GRANTED qos - 128/None means denied."""
        topics = [
            f"{self.base}/get/accepted",
            f"{self.base}/get/rejected",
            f"{self.base}/update/accepted",
            f"{self.base}/update/rejected",
            f"{self.base}/update/delta",
            f"{self.base}/update/documents",
        ]
        ok = 0
        for t in topics:
            try:
                fut, _ = self.conn.subscribe(t, mqtt.QoS.AT_LEAST_ONCE,
                                             self._on_msg)
                granted = fut.result(timeout=10).get("qos")
                # awscrt resolves the future even on a per-topic failure,
                # so check the granted qos rather than trusting no-exception.
                if granted is None or int(granted) > 2:
                    print(f"[-] sub denied  {t}  (granted={granted})")
                else:
                    print(f"[+] sub ok      {t}  (qos={int(granted)})")
                    ok += 1
            except Exception as e:
                print(f"[-] sub error   {t}  ({type(e).__name__}: {e})")
        return ok

    def request_shadow(self, timeout=10):
        """Publish an empty get - the response lands on get/accepted."""
        self.got_response.clear()
        self.conn.publish(f"{self.base}/get", b"{}",
                          mqtt.QoS.AT_LEAST_ONCE)[0].result(timeout=10)
        print(f"[*] published {self.base}/get - awaiting response")
        if not self.got_response.wait(timeout=timeout):
            print("[-] no response.  subscribe+publish both ACKed but nothing "
                  "was delivered -> iot:Receive likely missing from the "
                  "policy, or the policy was attached after this connection "
                  "opened (reconnect and retry).")
            return False
        return True

    def listen(self, seconds):
        print(f"[*] listening {seconds}s  (ctrl-c to stop)")
        try:
            time.sleep(seconds)
        except KeyboardInterrupt:
            print("\n[*] interrupted")

    def close(self):
        if self.conn:
            try:
                self.conn.disconnect().result(timeout=5)
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true",
                    help="keep listening for updates after the initial read")
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--thing", help="skip the device API, use this thing name")
    ap.add_argument("--show-policy", action="store_true",
                    help="dump the IoT policy document and exit")
    ap.add_argument("--no-attach", action="store_true",
                    help="skip attach_policy (to test whether it is needed)")
    ap.add_argument("--client-suffix", default="",
                    help="append to the mqtt client id, e.g. -obs, to run "
                         "alongside the phone app without session takeover")
    args = ap.parse_args()

    user = USERNAME or input("email: ")
    pw = PASSWORD or getpass.getpass("password: ")

    sess = Session(user, pw)
    print(f"[+] identity {sess.identity_id}")
    print(f"[+] creds expire {sess.expires_at}")

    if args.show_policy:
        show_policy(sess)
        return

    if not args.no_attach:
        attach_policy(sess)

    if args.thing:
        devices = [{"thingName": args.thing}]
    else:
        devices = list_devices(sess)
        print(f"[+] {len(devices)} device(s)")
        for d in devices:
            print(f"    {d['thingName']}  {d.get('modelName','')} "
                  f"{d.get('SN','')}")

    if not devices:
        print("[-] no devices returned")
        return

    thing = devices[0]["thingName"]
    sc = ShadowClient(sess, thing, args.client_suffix)
    try:
        sc.connect()
        if sc.subscribe_all() == 0:
            print("[-] every subscribe denied - confirm the policy attached, "
                  "then rerun")
            return
        sc.request_shadow()
        if args.watch:
            sc.listen(args.seconds)
    finally:
        sc.close()


if __name__ == "__main__":
    main()
