from __future__ import annotations

from lark_doc_whisper.state.user_doc_tokens import InMemoryUserDocTokenStore


def test_user_doc_token_store_returns_token_until_early_expiry():
    now = [1000.0]
    store = InMemoryUserDocTokenStore(now=lambda: now[0], expiry_skew_sec=300)

    store.put(
        user_open_id="ou_user",
        link_url="https://bytedance.sg.larkoffice.com/docx/link_doc",
        access_token="user-token",
        expires_in=7200,
    )

    assert store.get("ou_user", "https://bytedance.sg.larkoffice.com/docx/link_doc") == "user-token"
    assert store.get("ou_other", "https://bytedance.sg.larkoffice.com/docx/link_doc") is None

    now[0] = 7901.0

    assert store.get("ou_user", "https://bytedance.sg.larkoffice.com/docx/link_doc") is None


def test_user_doc_token_store_deletes_token():
    store = InMemoryUserDocTokenStore(now=lambda: 1000.0, expiry_skew_sec=300)
    store.put(
        user_open_id="ou_user",
        link_url="https://bytedance.sg.larkoffice.com/docx/link_doc",
        access_token="user-token",
        expires_in=7200,
    )

    store.delete("ou_user", "https://bytedance.sg.larkoffice.com/docx/link_doc")

    assert store.get("ou_user", "https://bytedance.sg.larkoffice.com/docx/link_doc") is None


def test_user_doc_token_store_prunes_all_expired_tokens():
    now = [1000.0]
    store = InMemoryUserDocTokenStore(now=lambda: now[0], expiry_skew_sec=300)
    store.put("ou_user", "https://example.com/docx/old", "old-token", expires_in=100)
    store.put("ou_user", "https://example.com/docx/fresh", "fresh-token", expires_in=7200)

    now[0] = 1200.0

    assert store.prune_expired() == 1
    assert store.get("ou_user", "https://example.com/docx/old") is None
    assert store.get("ou_user", "https://example.com/docx/fresh") == "fresh-token"
