from datetime import datetime, timezone

from chat_contract import normalize_chat_message


def test_image_message_exposes_general_ui_type_and_attachment_fields():
    created_at = datetime(2026, 8, 5, 19, 22, tzinfo=timezone.utc)
    result = normalize_chat_message({
        "id": 125664,
        "channel": "web_chat",
        "sender_name": "ADA",
        "content": "Prueba visual SOUL",
        "message_type": "image",
        "file_url": "/uploads/image_probe.png",
        "filename": "soul_logo_william.png",
        "created_at": created_at,
    })

    assert result["message_type"] == "image"
    assert result["type"] == "image"
    assert result["file_url"] == "/uploads/image_probe.png"
    assert result["filename"] == "soul_logo_william.png"
    assert result["created_at"] == created_at.isoformat()


def test_missing_message_type_falls_back_to_text():
    assert normalize_chat_message({"content": "hola"})["type"] == "text"
