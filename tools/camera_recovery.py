"""Sequential camera ownership with bounded backoff, independently testable."""
def supervise_capture(capture_once, state, stop, serial, detector=None, on_failure=None):
    attempt = 0
    while not stop.is_set():
        if detector:
            detector.invalidate()
        state.update({'state': 'CONNECTING', 'reconnect_attempt': attempt, 'real_output': False})
        try:
            capture_once(state, stop, serial, detector)
            if stop.is_set():
                break
            raise RuntimeError('Camera capture ended')
        except Exception as exc:
            # A healthy session resets backoff. A partially warmed session does not.
            if state.read()[0].get('preview_frames', 0) >= 30:
                attempt = 0
            attempt += 1
            delay = min(8, .5 * 2 ** min(attempt - 1, 4))
            if detector:
                detector.invalidate()
            if on_failure:
                on_failure()
            state.update({'state': 'RECONNECTING', 'error': str(exc), 'retry_seconds': delay,
                          'reconnect_attempt': attempt, 'real_output': False})
            stop.wait(delay)
    state.update({'state': 'STOPPED', 'real_output': False})
