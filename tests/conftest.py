"""Shared fixtures. pytest-homeassistant-custom-component provides `hass`."""

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Without this HA refuses to load anything in custom_components."""
    yield
