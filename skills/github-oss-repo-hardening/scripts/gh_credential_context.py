"""Presence-only GitHub CLI diagnostics; context equality is never authority.

No credential/config/Keychain contents are read or fingerprinted. Returned
identifiers are private evidence. See references/gh-credential-context.md.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
from typing import Mapping

SELECTORS = ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN')


class ContextError(ValueError):
    """A fixed, non-secret diagnostic code."""


def selected_source(host: str, environ: Mapping[str, str]) -> str:
    host = host.lower()
    names = SELECTORS[:2] if host == 'github.com' or host.endswith('.ghe.com') else SELECTORS[2:]
    return next((name for name in names if bool(environ.get(name))), 'stored')


def describe_context(*, environ: Mapping[str, str], surface: str,
                     target_host: str, effective_user: str, executor_host: str,
                     gh_path: str, gh_version: str) -> dict:
    """Pure adapter contract. Caller supplies observed identity, never secrets."""
    if not all(isinstance(v, str) and v for v in
               (surface, target_host, effective_user, executor_host, gh_path, gh_version)):
        raise ContextError('context_identity_missing')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', target_host):
        raise ContextError('target_host_invalid')
    if environ.get('GH_CONFIG_DIR'):
        source, config = 'GH_CONFIG_DIR', environ['GH_CONFIG_DIR']
    elif environ.get('XDG_CONFIG_HOME'):
        source, config = 'XDG_CONFIG_HOME', str(Path(environ['XDG_CONFIG_HOME']) / 'gh')
    elif os.name == 'nt' and environ.get('AppData'):
        source, config = 'AppData', str(Path(environ['AppData']) / 'GitHub CLI')
    else:
        if environ.get('HOME'):
            source, config = 'HOME', str(Path(environ['HOME']) / '.config/gh')
        elif selected_source(target_host, environ) != 'stored':
            source, config = 'unavailable', None
        else:
            raise ContextError('config_home_unknown')
    return dict(schema_version=1, surface=surface, target_host=target_host.lower(),
                effective_user=effective_user, executor_host=executor_host,
                gh_path=str(Path(gh_path).resolve()), gh_version=gh_version,
                config_source=source, config_dir=str(Path(config).absolute()) if config is not None else None,
                config_resolved=str(Path(config).resolve()) if config is not None else None,
                gh_host=environ.get('GH_HOST', ''),
                selectors={name: bool(environ.get(name)) for name in SELECTORS},
                credential_source=selected_source(target_host, environ))


def effective_user() -> str:
    """Observe the OS user; never trust environment usernames as identity."""
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes

            get_user = ctypes.WinDLL('advapi32.dll', use_last_error=True).GetUserNameW
            get_user.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            get_user.restype = wintypes.BOOL
            # UNLEN (256) plus terminator, per the Windows API contract.
            buffer = ctypes.create_unicode_buffer(257)
            size = wintypes.DWORD(len(buffer))
            if not get_user(buffer, ctypes.byref(size)) or not buffer.value:
                raise ContextError('effective_user_unavailable')
            return buffer.value
        return str(os.geteuid())
    except (OSError, AttributeError):
        raise ContextError('effective_user_unavailable') from None


def observe_context(*, surface: str, target_host: str) -> dict:
    """Observe this executor without running auth commands or reading config."""
    gh = shutil.which('gh')
    if not gh:
        raise ContextError('gh_not_found')
    try:
        result = subprocess.run([str(Path(gh).resolve()), '--version'],
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        raise ContextError('gh_version_unavailable') from None
    match = re.match(r'gh version ([0-9]+\.[0-9]+\.[0-9]+)\b', result.stdout)
    if result.returncode or not match:
        raise ContextError('gh_version_unavailable')
    return describe_context(environ=os.environ, surface=surface, target_host=target_host,
                            effective_user=effective_user(), executor_host=socket.gethostname(),
                            gh_path=gh, gh_version=match[1])


def require_same_context(reviewed: dict, current: dict) -> None:
    if not isinstance(reviewed, dict) or reviewed != current:
        raise ContextError('context_drift')


def classify_observation(*, http_status: int | None = None, historical: bool = False,
                         authentication_probe: bool = False,
                         runtime_allowed: bool | None = None,
                         feature_available: bool | None = None,
                         permission_sufficient: bool | None = None,
                         transport_failed: bool = False) -> str:
    """Classify a caller's observation, without inferring permissions from reads."""
    if historical:
        return 'historical_unresolved_401' if http_status == 401 else 'historical_unresolved'
    if runtime_allowed is False:
        return 'runtime_denied'
    if feature_available is False:
        return 'feature_unavailable'
    if permission_sufficient is False:
        return 'insufficient_permissions'
    if transport_failed:
        return 'transport_failure'
    if http_status == 401:
        return 'authentication_failed'
    if http_status == 403:
        return 'permission_or_policy_denied'
    if http_status == 404:
        return 'not_found_or_inaccessible'
    if http_status is not None and 200 <= http_status < 300:
        return 'authentication_succeeded' if authentication_probe else 'request_succeeded'
    return 'unknown'
