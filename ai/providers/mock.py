"""HG-078 (A0): ElevenLabs with its network swapped for an in-process answer (development and tests).

``MockElevenLabsTTS`` *is* ``ElevenLabsTTS``: only ``_http`` differs, so the request it records is the one the live
client builds. Nothing here opens a socket. The answer is always the same small, valid, silent MP3.
"""

import json

from .base import ProviderError
from .elevenlabs import ElevenLabsTTS

#: One MPEG-1 Layer III frame: 32 kbit/s, 32 kHz, mono, no CRC (header FF FB 18 C0), zero side information and zero
#: main data, which decodes to 1152 samples of silence. 144 bytes per frame; ten frames are 0.36 s.
SILENT_FRAME = bytes((0xFF, 0xFB, 0x18, 0xC0)) + bytes(140)
SILENT_MP3 = SILENT_FRAME * 10


class MockElevenLabsTTS(ElevenLabsTTS):
    code = "elevenlabs_tts_mock"
    live = False

    def __init__(self, voice_id, model_id, fail_with=None):
        super().__init__(api_key="mock-api-key", voice_id=voice_id, model_id=model_id,
                         base_url="https://api.elevenlabs.invalid")
        self.fail_with = fail_with  # a test can make the "provider" refuse
        self.calls = []

    def _http(self, request):
        self.calls.append({"method": request.get_method(), "url": request.full_url,
                           "body": json.loads(request.data.decode("utf-8"))})
        if self.fail_with:
            raise ProviderError(self.fail_with)
        return "audio/mpeg", SILENT_MP3
