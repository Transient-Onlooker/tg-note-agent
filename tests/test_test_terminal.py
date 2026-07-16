from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pypdf

from app.models.schemas import TextAnalysisResult
from scripts.test_terminal import (
    PdfExtractionResult,
    TerminalTelegramClient,
    _build_pdf_create_text,
    _build_photo_update,
    _extract_pdf_pages,
    _parse_attachment_command,
)


class FakeVisionProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.image_bytes: list[bytes] = []

    def analyze_image(
        self,
        image_path: str,
        caption: str | None = None,
    ) -> TextAnalysisResult:
        self.calls.append(caption or "")
        self.image_bytes.append(Path(image_path).read_bytes())
        return TextAnalysisResult(
            title="OCR page",
            summary="OCR page",
            tags=["pdf"],
            confidence=0.9,
            raw_response="{}",
            ocr_text="scanned page text",
            is_note=True,
        )


class FakePdfObject(dict):
    def __init__(
        self,
        *args,
        data: bytes = b"",
        text: str = "",
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.data = data
        self.text = text

    def get_object(self):
        return self

    def get_data(self) -> bytes:
        return self.data

    def extract_text(self) -> str:
        return self.text


def test_parse_attachment_command_supports_spaces_and_caption() -> None:
    parsed = _parse_attachment_command(
        '/image "C:\\test folder\\note.jpg" | biology note'
    )

    assert parsed is not None
    command, path, caption = parsed
    assert command == "/image"
    assert path == Path(r"C:\test folder\note.jpg")
    assert caption == "biology note"


def test_build_photo_update_serves_local_file(tmp_path: Path) -> None:
    image_path = tmp_path / "note.jpg"
    image_path.write_bytes(b"local image bytes")
    telegram_client = TerminalTelegramClient()

    update = _build_photo_update(
        message_id=99,
        chat_id=777,
        user_id=123,
        path=image_path,
        caption="photo note",
        telegram_client=telegram_client,
    )

    message = update.message
    assert message is not None
    assert message.photo is not None
    photo = message.photo[0]
    assert message.caption == "photo note"
    assert telegram_client.download_file(
        telegram_client.get_file_path(photo.file_id)
    ) == b"local image bytes"


def test_pdf_extraction_uses_embedded_text_then_vision_ocr(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pdf_path = tmp_path / "mixed.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    image = FakePdfObject(
        {
            "/Subtype": "/Image",
            "/Filter": "/DCTDecode",
        },
        data=b"embedded scan image",
    )
    pages = [
        FakePdfObject(
            text="This page contains enough embedded text for direct extraction."
        ),
        FakePdfObject(
            {
                "/Resources": FakePdfObject(
                    {
                        "/XObject": FakePdfObject(
                            {
                                "/Im0": image,
                            }
                        )
                    }
                )
            }
        ),
    ]
    monkeypatch.setattr(
        pypdf,
        "PdfReader",
        lambda _: SimpleNamespace(pages=pages),
    )
    provider = FakeVisionProvider()

    result = _extract_pdf_pages(
        pdf_path,
        nim_provider=provider,
        caption="mixed PDF",
        max_pages=2,
        min_embedded_chars=20,
    )

    assert result.processed_pages == 2
    assert "[Page 1 | embedded text]" in result.text
    assert "[Page 2 | vision OCR]\nscanned page text" in result.text
    assert result.ocr_pages == [2]
    assert result.failed_pages == []
    assert len(provider.calls) == 1
    assert provider.image_bytes == [b"embedded scan image"]


def test_pdf_create_text_reports_character_limit() -> None:
    extraction = PdfExtractionResult(
        text="abcdefghij",
        total_pages=3,
        processed_pages=2,
        ocr_pages=[2],
        failed_pages=[],
        page_limit_reached=True,
    )

    message, was_truncated = _build_pdf_create_text(
        path=Path("report.pdf"),
        extraction=extraction,
        caption="statistics report",
        max_chars=6,
    )

    assert was_truncated is True
    assert message == (
        "/new statistics report\n\n"
        "[PDF: report.pdf | processed 2/3 pages]\n\nabcdef"
    )
