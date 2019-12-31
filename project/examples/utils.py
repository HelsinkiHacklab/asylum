# -*- coding: utf-8 -*-
from holviapp.utils import api_configured, get_connection


def get_holvi_singleton():
    """DEPRECATED proxies to holviapp.utils.get_connection"""
    if not api_configured():
        return False
    return get_connection()
