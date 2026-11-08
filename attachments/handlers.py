"""Stop an oversized upload while it streams, before Django spools it to memory or a temp file."""

from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload

#: Multipart framing and the form's small fields, on top of the file itself.
OVERHEAD = 64 * 1024


def too_big_for_the_cap(content_length):
    try:
        return int(content_length or 0) > int(settings.ATTACHMENTS_MAX_BYTES) + OVERHEAD
    except (TypeError, ValueError):
        return False


class CapUploadHandler(FileUploadHandler):
    """First in the list: counts the file's bytes and stops the upload once they pass ``ATTACHMENTS_MAX_BYTES``."""

    def __init__(self, request=None):
        super().__init__(request)
        self.exceeded = False
        self.seen = 0

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.seen = 0

    def receive_data_chunk(self, raw_data, start):
        self.seen += len(raw_data)
        if self.seen > int(settings.ATTACHMENTS_MAX_BYTES):
            self.exceeded = True
            raise StopUpload(connection_reset=False)  # the rest is read and dropped, never kept
        return raw_data

    def file_complete(self, file_size):
        return None
