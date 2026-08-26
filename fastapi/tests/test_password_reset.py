import pytest
from fastapi import HTTPException

from app.auth import (
    create_password_reset_token,
    decode_password_reset_token,
    hash_password,
)
from app.core.email import build_password_reset_url


def test_password_reset_token_is_bound_to_current_password():
    old_hash = hash_password('OldPassword1!')
    token = create_password_reset_token(42, old_hash)

    assert decode_password_reset_token(token, old_hash) == 42

    with pytest.raises(HTTPException) as error:
        decode_password_reset_token(token, hash_password('NewPassword1!'))
    assert error.value.status_code == 400


def test_password_reset_url_points_to_frontend(monkeypatch):
    monkeypatch.setitem(__import__('app.core.email', fromlist=['settings']).settings, 'frontend_url', 'https://app.example.com/')

    assert build_password_reset_url('token-value') == 'https://app.example.com/reset-password?token=token-value'
