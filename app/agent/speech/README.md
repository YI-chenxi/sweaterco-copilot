# Voice input backend

The native Streamlit recorder submits a WAV file. `SpeechService` validates an
uncompressed recording (maximum 5 MiB and 60 seconds), then uses the configured
file transcription model. `SPEECH_MODEL` defaults to `gpt-transcribe` and
`SPEECH_TIMEOUT_SECONDS` defaults to 30. The server uses `OPENAI_API_KEY`; the
key is never sent to the browser. Audio is kept only for the current request.

The UI stores the transcript as an editable draft in the originating chat.
Transcription does not call the Agent. The reviewed text enters the existing
chat path only when the user clicks **Send reviewed text**. If a submission
contains both typed text and audio, the typed text is kept above the transcript
in that editable draft. A page rerun does not retranscribe the same submission.

Run offline tests with `python -m pytest tests/test_speech_service.py
tests/test_streamlit_speech.py`. For live validation, configure a working API key
and transcription model, run Streamlit, and record short Chinese, English, and
mixed-language phrases. Check order IDs, quantities, dates, negations, empty
recordings, and chat switching. Record actual transcription, elapsed time, and
error category separately from Mock test results. Microphone permission and a
secure browser context are required for a deployed site.

The first version has no streaming transcription, audio archive, arbitrary file
upload, or business write on recording submission. Speech recognition can be
wrong; the user must review the draft before sending it.
