"""Audio recording journeys; RUN_BROWSER_TESTS=1 uses a local Chromium.

Render the production form template and load its real scripts/styles. Requests
are intercepted so these tests do not require a database or application server.
"""

import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape
import pytest


ROOT = Path(__file__).resolve().parents[1]
MEDIA_MOCK = """
window.tracks = [];
window.denied = false;
window.deferPermission = false;
Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {value: async () => {
  if (window.denied) throw new DOMException('Denied', 'NotAllowedError');
  const track = new EventTarget(); track.stopped = false;
  track.stop = () => { track.stopped = true; };
  window.tracks.push(track);
  const stream = {getTracks: () => [track], getAudioTracks: () => [track]};
  if (window.deferPermission) return new Promise(resolve => { window.grant = () => resolve(stream); });
  return stream;
}});
window.MediaRecorder = class extends EventTarget {
  static isTypeSupported(type) { return type === window.recordingType; }
  constructor(stream, options) {
    super(); this.state = 'inactive'; this.mimeType = options?.mimeType || 'audio/webm';
    window.recorder = this;
  }
  start() { this.state = 'recording'; }
  stop() {
    if (this.state === 'inactive') throw new Error('Already stopped');
    this.state = 'inactive';
    setTimeout(() => {
      const data = new Event('dataavailable');
      data.data = new Blob(['recorded audio'], {type: this.mimeType});
      this.dispatchEvent(data); this.dispatchEvent(new Event('stop'));
    }, 10);
  }
};
window.recordingType = 'audio/mp4';
"""


@pytest.fixture(scope="module")
def browser():
    if os.environ.get("RUN_BROWSER_TESTS") != "1":
        pytest.skip("Set RUN_BROWSER_TESTS=1 to run browser checks")
    api = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or shutil.which("chromium")
    if not executable:
        pytest.skip("Chromium is required")
    with api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, args=[
            "--no-sandbox", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
        ])
        yield browser
        browser.close()


@pytest.fixture
def mount(browser):
    contexts = []
    errors = []

    def create(*, width=390, mock=True, unsupported=False, file_required=False, java_script_enabled=True, response_status=200):
        environment = Environment(loader=ChoiceLoader([
            DictLoader({"base.html": """<!DOCTYPE html><html><head>
              <meta name="viewport" content="width=device-width, initial-scale=1">
              <link rel="stylesheet" href="/static/css/app.css">
              {% block styles %}{% endblock %}</head><body class="theme-default">
              {% block content %}{% endblock %}{% block scripts %}{% endblock %}</body></html>"""}),
            FileSystemLoader(ROOT / "app/templates"),
        ]), autoescape=select_autoescape())
        html = environment.get_template("forms/submit.html").render(
            form={"name": "Support", "slug": "support", "description": "Describe the issue."},
            fields=[{"id": 11, "label": "Voice message", "field_type": "audio", "required": True, "config": {"max_clips": 2}},
                    {"id": 12, "label": "More details", "field_type": "audio", "required": False, "config": {"max_clips": 1}},
                    {"id": 13, "label": "Evidence", "field_type": "file", "required": file_required, "config": {}}],
            errors=[], static_url=lambda path: path,
        )
        context = browser.new_context(viewport={"width": width, "height": 850}, java_script_enabled=java_script_enabled)
        contexts.append(context)
        if mock:
            context.add_init_script(MEDIA_MOCK + ("window.MediaRecorder = undefined;" if unsupported else ""))
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        submitted = []

        def intercept(route):
            path = urlsplit(route.request.url).path
            if route.request.method == "POST":
                submitted.append(route.request.post_data_buffer)
                route.fulfill(status=response_status, content_type="application/json", body='{"ticket_id":42,"detail":"Please try again"}')
            elif path.startswith("/static/"):
                asset = ROOT / "app" / path.lstrip("/")
                if asset.is_file():
                    route.fulfill(content_type="text/css" if asset.suffix == ".css" else "text/javascript", body=asset.read_bytes())
                else:
                    route.fulfill(status=204)
            else:
                route.fulfill(content_type="text/html", body=html, headers={
                    "Permissions-Policy": "microphone=(self), camera=()",
                    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; img-src 'self' data: blob:",
                })

        context.route("**/*", intercept)
        page.goto("https://portal.test/forms/support")
        return page, submitted

    yield create
    for context in contexts:
        context.close()
    assert not errors, errors


def record_clip(page, field=11):
    wrap = page.locator(f'[data-field-id="{field}"]')
    wrap.get_by_role("button", name="Record audio", exact=True).click()
    wrap.get_by_role("button", name="Stop recording").wait_for()
    wrap.get_by_role("button", name="Stop recording").click()
    wrap.locator("audio").wait_for()
    return wrap


@pytest.mark.parametrize("width", [390, 1280])
def test_record_upload_remove_and_submit(mount, width):
    page, submitted = mount(width=width)
    wrap = record_clip(page)
    assert wrap.locator("audio").get_attribute("src").startswith("blob:")
    assert page.evaluate("tracks.every(track => track.stopped)")
    wrap.locator('input[type="file"]').set_input_files({"name": "upload.wav", "mimeType": "audio/wav", "buffer": b"uploaded audio"})
    assert wrap.locator("audio").count() == 2
    assert wrap.get_by_role("button", name="Record audio", exact=True).is_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert wrap.get_by_role("button", name="Remove recording-1.m4a").bounding_box()["height"] >= 44
    wrap.get_by_role("button", name="Remove recording-1.m4a").click()
    record_clip(page)
    page.get_by_role("button", name="Submit request").click()
    page.get_by_role("link", name="View ticket #42").wait_for()
    assert len(submitted) == 1
    assert b'filename="upload.wav"' in submitted[0]
    assert b'filename="recording-2.m4a"' in submitted[0]
    assert b"recorded audio" in submitted[0]
    assert b'filename="recording-1.m4a"' not in submitted[0]
    assert page.locator("audio").count() == 0


def test_required_recording_and_active_recording_block_submission(mount):
    page, submitted = mount()
    page.get_by_role("button", name="Submit request").click()
    assert page.locator('[data-field-id="11"] [data-cf-field-error]').is_visible()
    wrap = page.locator('[data-field-id="11"]')
    wrap.get_by_role("button", name="Record audio", exact=True).click()
    wrap.get_by_role("button", name="Stop recording").wait_for()
    page.get_by_role("button", name="Submit request").click()
    assert "Stop recording and wait" in page.locator("[data-cf-status]").inner_text()
    assert not submitted
    assert wrap.locator('input[type="file"]').is_disabled()


def test_permission_denial_keeps_upload_available(mount):
    page, _ = mount()
    page.evaluate("window.denied = true")
    wrap = page.locator('[data-field-id="11"]')
    wrap.get_by_role("button", name="Record audio", exact=True).click()
    page.wait_for_function("() => document.querySelector('[data-cf-audio-status]').textContent.includes('denied')")
    assert wrap.locator('input[type="file"]').is_enabled()
    assert wrap.get_by_role("button", name="Record audio", exact=True).is_enabled()


@pytest.mark.parametrize("event", ["reset", "pagehide"])
def test_cleanup_during_pending_permission(mount, event):
    page, _ = mount()
    page.evaluate("window.deferPermission = true")
    page.locator('[data-field-id="11"] [data-cf-audio-record]').click()
    page.wait_for_function("() => !!window.grant")
    page.evaluate("document.querySelector('form').reset()" if event == "reset" else "window.dispatchEvent(new Event('pagehide'))")
    page.evaluate("window.grant()")
    page.wait_for_function("() => tracks.every(track => track.stopped)")
    assert page.locator("audio").count() == 0
    assert page.locator('[data-field-id="11"] [data-cf-audio-record]').is_enabled()


def test_recording_error_and_failed_submission_preserve_existing_clips(mount):
    page, submitted = mount(response_status=422)
    wrap = record_clip(page)
    wrap.get_by_role("button", name="Record audio", exact=True).click()
    wrap.get_by_role("button", name="Stop recording").wait_for()
    page.evaluate("window.recorder.dispatchEvent(new Event('error'))")
    page.wait_for_function("() => tracks.every(track => track.stopped)")
    assert wrap.locator("audio").count() == 1
    page.get_by_role("button", name="Submit request").click()
    page.wait_for_function("() => document.querySelector('[data-cf-status]').textContent.includes('Please try again')")
    assert len(submitted) == 1
    assert wrap.locator("audio").count() == 1
    assert wrap.get_by_role("button", name="Record audio", exact=True).is_enabled()


def test_uploaded_clips_cannot_exceed_limit(mount):
    page, _ = mount()
    wrap = record_clip(page)
    wrap.locator('input[type="file"]').set_input_files([
        {"name": name, "mimeType": "audio/wav", "buffer": b"audio"} for name in ["one.wav", "two.wav"]
    ])
    assert "up to 2 clips" in wrap.locator("[data-cf-audio-status]").inner_text()
    assert wrap.locator("audio").count() == 1


def test_no_javascript_keeps_required_upload(mount):
    page, _ = mount(java_script_enabled=False)
    assert page.locator('[data-field-id="11"] input').get_attribute("required") is not None
    assert page.locator('[data-field-id="11"] [data-cf-audio-controls]').is_hidden()


def test_unsupported_browser_keeps_upload_and_submission(mount):
    page, submitted = mount(unsupported=True)
    wrap = page.locator('[data-field-id="11"]')
    assert wrap.locator('[data-cf-audio-controls]').is_hidden()
    assert "unavailable" in wrap.locator('[data-cf-audio-status]').inner_text()
    wrap.locator('input[type="file"]').set_input_files({"name": "fallback.wav", "mimeType": "audio/wav", "buffer": b"audio"})
    page.get_by_role("button", name="Submit request").click()
    page.get_by_role("link", name="View ticket #42").wait_for()
    assert b'filename="fallback.wav"' in submitted[0]


def test_only_one_microphone_session_and_reset_stops_it(mount):
    page, _ = mount()
    first = page.locator('[data-field-id="11"]')
    second = page.locator('[data-field-id="12"]')
    first.locator('[data-cf-audio-record]').click()
    first.locator('[data-cf-audio-stop]').wait_for()
    second.locator('[data-cf-audio-record]').click()
    assert "other recording" in second.locator('[data-cf-audio-status]').inner_text()
    assert page.evaluate("tracks.length") == 1
    page.evaluate("document.querySelector('form').reset()")
    page.wait_for_function("() => tracks.every(track => track.stopped)")
    assert page.locator('audio').count() == 0
    assert first.locator('[data-cf-audio-record]').is_enabled()


def test_oversized_recording_is_discarded(mount):
    page, _ = mount()
    wrap = page.locator('[data-field-id="11"]')
    wrap.locator('[data-cf-audio-record]').click()
    wrap.locator('[data-cf-audio-stop]').wait_for()
    page.evaluate("""() => {
        const event = new Event('dataavailable');
        event.data = {size: 50 * 1024 * 1024 + 1};
        window.recorder.dispatchEvent(event);
    }""")
    page.wait_for_function("() => document.querySelector('[data-cf-audio-status]').textContent.includes('exceeded')")
    assert wrap.locator('audio').count() == 0
    assert page.evaluate("tracks.every(track => track.stopped)")


def test_real_media_recorder_captures_audio(mount):
    page, _ = mount(mock=False)
    wrap = page.locator('[data-field-id="11"]')
    wrap.get_by_role("button", name="Record audio", exact=True).click()
    wrap.get_by_role("button", name="Stop recording").wait_for()
    page.wait_for_function("() => document.querySelector('[data-cf-audio-timer]').textContent !== '0:00'")
    wrap.get_by_role("button", name="Stop recording").click()
    wrap.locator("audio").wait_for()
    page.wait_for_function("() => document.querySelector('audio').readyState >= 1")
    assert page.evaluate("document.querySelector('audio').error === null")


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("kind, label, mime, filename", [
    ("photo", "Take photo", "image/jpeg", "photo.jpg"),
    ("video", "Record video", "video/mp4", "video.mp4"),
])
def test_camera_picker_preview_and_submit(mount, width, kind, label, mime, filename):
    page, submitted = mount(width=width, file_required=True)
    record_clip(page)
    wrap = page.locator('[data-field-id="13"]')
    button = wrap.get_by_role('button', name=label)
    assert button.bounding_box()['height'] >= 44
    with page.expect_file_chooser() as chooser:
        button.click()
    assert chooser.value.element.get_attribute('capture') == 'environment'
    assert chooser.value.element.get_attribute('accept') == ('image/*' if kind == 'photo' else 'video/*')
    chooser.value.set_files({'name': filename, 'mimeType': mime, 'buffer': b'camera attachment'})
    media = wrap.locator('img' if kind == 'photo' else 'video')
    assert media.get_attribute('src').startswith('blob:')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('button', name='Submit request').click()
    page.get_by_role('link', name='View ticket #42').wait_for()
    assert f'filename="{filename}"'.encode() in submitted[0]
    assert b'name="file_13"' in submitted[0]
    assert b'camera attachment' in submitted[0]
    assert wrap.locator('[data-cf-capture-preview]').inner_text() == ''


def test_capture_replaces_upload_cancel_preserves_and_remove_validates(mount):
    page, submitted = mount(file_required=True)
    record_clip(page)
    wrap = page.locator('[data-field-id="13"]')
    wrap.locator('[data-cf-file]').set_input_files({'name': 'existing.pdf', 'mimeType': 'application/pdf', 'buffer': b'document'})
    wrap.locator('[data-cf-photo-input]').set_input_files({'name': 'new.jpg', 'mimeType': 'image/jpeg', 'buffer': b'photo'})
    assert 'existing.pdf' not in wrap.inner_text()
    wrap.locator('[data-cf-video-input]').set_input_files([])
    assert 'new.jpg' in wrap.locator('[data-cf-capture-preview]').inner_text()
    wrap.get_by_role('button', name='Remove attachment').click()
    page.get_by_role('button', name='Submit request').click()
    assert not submitted
    assert wrap.locator('[data-cf-field-error]').is_visible()
    wrap.locator('[data-cf-video-input]').set_input_files({'name': 'new.mov', 'mimeType': 'video/quicktime', 'buffer': b'video'})
    page.get_by_role('button', name='Submit request').click()
    page.get_by_role('link', name='View ticket #42').wait_for()
    assert b'filename="new.mov"' in submitted[0]
    assert b'filename="existing.pdf"' not in submitted[0]
    assert b'filename="new.jpg"' not in submitted[0]


def test_heic_capture_and_failed_submission_keep_attachment(mount):
    page, submitted = mount(response_status=422)
    record_clip(page)
    wrap = page.locator('[data-field-id="13"]')
    wrap.locator('[data-cf-photo-input]').set_input_files({'name': 'photo.heic', 'mimeType': 'image/heic', 'buffer': b'heic photo'})
    assert wrap.locator('img').count() == 0
    page.get_by_role('button', name='Submit request').click()
    page.wait_for_function("() => document.querySelector('[data-cf-status]').textContent.includes('Please try again')")
    assert b'filename="photo.heic"' in submitted[0]
    assert 'photo.heic' in wrap.locator('[data-cf-capture-preview]').inner_text()
    assert wrap.get_by_role('button', name='Take photo').is_enabled()
    page.evaluate("document.querySelector('form').reset()")
    assert wrap.locator('[data-cf-capture-preview]').inner_text() == ''


def test_oversized_camera_attachment_keeps_previous_file(mount):
    page, _ = mount()
    wrap = page.locator('[data-field-id="13"]')
    wrap.locator('[data-cf-file]').set_input_files({'name': 'keep.pdf', 'mimeType': 'application/pdf', 'buffer': b'document'})
    page.evaluate("""() => {
      const input = document.querySelector('[data-cf-video-input]');
      const files = new DataTransfer();
      files.items.add(new File([new Uint8Array(50 * 1024 * 1024 + 1)], 'large.mp4', {type: 'video/mp4'}));
      input.files = files.files; input.dispatchEvent(new Event('change'));
    }""")
    assert '50 MB or smaller' in wrap.locator('[data-cf-capture-status]').inner_text()
    assert 'keep.pdf' in wrap.locator('[data-cf-capture-preview]').inner_text()


def test_no_javascript_file_upload_remains_required(mount):
    page, _ = mount(java_script_enabled=False, file_required=True)
    wrap = page.locator('[data-field-id="13"]')
    assert wrap.locator('[data-cf-file]').get_attribute('required') is not None
    assert wrap.locator('[data-cf-capture-controls]').is_hidden()
