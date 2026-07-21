from entities.attachment.business.service import AttachmentService


def test_build_blob_name_with_extension():
    assert (
        AttachmentService.build_blob_name(
            "d3091b54-121b-4d10-bc96-649db4e946fe", "pdf"
        )
        == "d3091b54-121b-4d10-bc96-649db4e946fe.pdf"
    )


def test_build_blob_name_preserves_extension_case():
    assert AttachmentService.build_blob_name("abc", "PDF") == "abc.PDF"


def test_build_blob_name_none_extension():
    assert AttachmentService.build_blob_name("abc", None) == "abc"


def test_build_blob_name_empty_extension():
    assert AttachmentService.build_blob_name("abc", "") == "abc"


def test_build_blob_name_dot_before_extension():
    name = AttachmentService.build_blob_name("abc", "pdf")
    assert name == "abc.pdf"
    assert name.endswith(".pdf")
    assert name != "abcpdf"
