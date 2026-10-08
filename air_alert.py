"""
Air Alert Manager for Kyiv City (Google Meet Streamer integration).
Monitors official Kyiv Digital API: https://kyiv.digital/open-api/air-alert/state
Handles automated and manual alert lifecycle:
- Red / Yellow alert detection and chat announcements
- Stream banner updates with start time and indicator circle
- All-clear (відбій) announcements with duration calculation and lesson resumption:
  - Duration <= 10 min -> lesson resumes in 10 minutes
  - Duration > 10 min -> lesson resumes in 20 minutes
- Automated restoration of normal banner when lesson resumes
"""

import os
import json
import time
import threading
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    KYIV_TZ = ZoneInfo("Europe/Kyiv")
except Exception:
    KYIV_TZ = timezone(timedelta(hours=3))

API_URL = "https://kyiv.digital/open-api/air-alert/state"


def get_kyiv_now():
    """Return current datetime in Kyiv timezone."""
    return datetime.now(KYIV_TZ)


class AirAlertManager:
    def __init__(self, state_file=None, get_banner_fn=None, apply_banner_fn=None, send_chat_fn=None, is_in_meeting_fn=None):
        self.state_file = state_file or "/app/air_alert_state.json"
        self.get_banner_fn = get_banner_fn
        self.apply_banner_fn = apply_banner_fn
        self.send_chat_fn = send_chat_fn
        self.is_in_meeting_fn = is_in_meeting_fn

        self.lock = threading.Lock()
        self.monitoring_enabled = True
        self.manual_override = False

        # Alert state
        self.is_alert = False
        self.alert_level = None       # "red", "yellow", None
        self.alert_causes = []        # e.g. ["drone"], ["missile"]
        self.alert_start_time = None  # "HH:MM"
        self.alert_start_dt = None    # datetime object
        self.alert_end_time = None    # "HH:MM"
        self.alert_end_dt = None      # datetime object
        self.alert_duration = None    # minutes (int)
        self.lesson_resumes_at = None # "HH:MM"
        self.lesson_resume_dt = None  # datetime object
        self.resumed_banner_restored = False

        self.last_checked_at = None
        self.last_api_state = 0
        self.last_error = None

        self._load_state()
        self._thread = None
        self._stop_event = threading.Event()

    def _load_state(self):
        if self.state_file and os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.monitoring_enabled = bool(data.get("monitoring_enabled", True))
                    self.manual_override = bool(data.get("manual_override", False))
                    self.is_alert = bool(data.get("is_alert", False))
                    self.alert_level = data.get("alert_level")
                    self.alert_causes = data.get("alert_causes", [])
                    self.alert_start_time = data.get("alert_start_time")
                    self.alert_end_time = data.get("alert_end_time")
                    self.alert_duration = data.get("alert_duration")
                    self.lesson_resumes_at = data.get("lesson_resumes_at")
            except Exception as e:
                print(f"[AirAlert] Error loading state from {self.state_file}: {e}")

    def _save_state(self):
        if not self.state_file:
            return
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            data = {
                "monitoring_enabled": self.monitoring_enabled,
                "manual_override": self.manual_override,
                "is_alert": self.is_alert,
                "alert_level": self.alert_level,
                "alert_causes": self.alert_causes,
                "alert_start_time": self.alert_start_time,
                "alert_end_time": self.alert_end_time,
                "alert_duration": self.alert_duration,
                "lesson_resumes_at": self.lesson_resumes_at,
                "last_checked_at": self.last_checked_at,
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[AirAlert] Error saving state: {e}")

    def start(self):
        """Start background polling thread."""
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._worker_loop, name="AirAlertWorker", daemon=True)
            self._thread.start()
            print("[AirAlert] Worker thread started")

    def stop(self):
        """Stop background polling thread."""
        self._stop_event.set()

    def get_status(self):
        """Return public dictionary of current alert status."""
        with self.lock:
            return {
                "monitoring_enabled": self.monitoring_enabled,
                "manual_override": self.manual_override,
                "is_alert": self.is_alert,
                "alert_level": self.alert_level,
                "alert_causes": self.alert_causes,
                "alert_start_time": self.alert_start_time,
                "alert_end_time": self.alert_end_time,
                "alert_duration": self.alert_duration,
                "lesson_resumes_at": self.lesson_resumes_at,
                "last_checked_at": self.last_checked_at,
                "last_error": self.last_error,
            }

    def set_monitoring(self, enabled: bool):
        """Toggle monitoring on/off."""
        with self.lock:
            self.monitoring_enabled = bool(enabled)
            self._save_state()
        return self.get_status()

    def trigger_manual_alert(self, level: str = "red"):
        """Manually trigger an alert (red or yellow)."""
        lvl = "yellow" if level == "yellow" else "red"
        now = get_kyiv_now()
        start_time_str = now.strftime("%H:%M")

        with self.lock:
            self.manual_override = True
            self.is_alert = True
            self.alert_level = lvl
            self.alert_causes = ["manual"]
            self.alert_start_time = start_time_str
            self.alert_start_dt = now
            self.alert_end_time = None
            self.alert_end_dt = None
            self.alert_duration = None
            self.lesson_resumes_at = None
            self.lesson_resume_dt = None
            self.resumed_banner_restored = False
            self._save_state()

        self._broadcast_alert_start(lvl, is_manual=True)
        return self.get_status()

    def trigger_manual_clear(self):
        """Manually announce all-clear (відбій)."""
        now = get_kyiv_now()
        end_time_str = now.strftime("%H:%M")

        with self.lock:
            self.manual_override = True
            self.is_alert = False

            # Calculate duration
            if self.alert_start_dt:
                delta_sec = max(0, (now - self.alert_start_dt).total_seconds())
                duration_min = max(1, int(round(delta_sec / 60.0)))
            elif self.alert_start_time:
                try:
                    start_parts = [int(p) for p in self.alert_start_time.split(":")]
                    start_est = now.replace(hour=start_parts[0], minute=start_parts[1], second=0)
                    if start_est > now:
                        start_est -= timedelta(days=1)
                    duration_min = max(1, int(round((now - start_est).total_seconds() / 60.0)))
                except Exception:
                    duration_min = 15
            else:
                duration_min = 15

            # Calculate lesson resume offset
            delay_min = 10 if duration_min <= 10 else 20
            resume_dt = now + timedelta(minutes=delay_min)
            resume_time_str = resume_dt.strftime("%H:%M")

            self.alert_end_time = end_time_str
            self.alert_end_dt = now
            self.alert_duration = duration_min
            self.alert_level = "green"
            self.lesson_resumes_at = resume_time_str
            self.lesson_resume_dt = resume_dt
            self.resumed_banner_restored = False
            self._save_state()

        self._broadcast_alert_end(end_time_str, duration_min, resume_time_str, delay_min)
        return self.get_status()

    def reset_to_auto(self):
        """Reset manual override back to automatic polling."""
        with self.lock:
            self.manual_override = False
            self.resumed_banner_restored = False
            self._save_state()

        # Immediate poll
        self._check_api()
        return self.get_status()

    def reset_banner_to_normal(self):
        """Clear any alert overlay from the banner and restore default poster."""
        with self.lock:
            self.is_alert = False
            self.alert_level = None
            self.alert_end_time = None
            self.alert_duration = None
            self.lesson_resumes_at = None
            self.resumed_banner_restored = True
            self._save_state()

        self._apply_stream_banner(alert_level=None)
        return self.get_status()

    def _worker_loop(self):
        while not self._stop_event.is_set():
            try:
                now = get_kyiv_now()

                # Check if lesson resumption time reached -> auto restore banner
                if (
                    not self.is_alert
                    and self.lesson_resume_dt
                    and not self.resumed_banner_restored
                ):
                    if now >= self.lesson_resume_dt:
                        print(f"[AirAlert] Lesson resumption time {self.lesson_resumes_at} reached. Restoring normal banner.")
                        with self.lock:
                            self.resumed_banner_restored = True
                            self._save_state()
                        self._apply_stream_banner(alert_level=None)
                        self._send_meet_chat("🔔 Урок починається!")

                # If automated monitoring is enabled and not manually overridden
                if self.monitoring_enabled and not self.manual_override:
                    self._check_api()

            except Exception as e:
                print(f"[AirAlert] Worker loop error: {e}")

            # Sleep 10 seconds before next check
            self._stop_event.wait(10)

    def _check_api(self):
        now = get_kyiv_now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        req = urllib.request.Request(
            API_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": "CICD-AirAlertMonitor/1.0"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status != 200:
                    with self.lock:
                        self.last_error = f"HTTP {resp.status}"
                    return
                body = resp.read().decode('utf-8')
                data = json.loads(body)
        except Exception as e:
            with self.lock:
                self.last_error = str(e)
            return

        with self.lock:
            self.last_checked_at = now_str
            self.last_error = None

        current = data.get("current", {})
        api_state = int(current.get("state", 0))
        causes = current.get("causes", [])
        last_cause = current.get("last_cause")
        created_at_raw = current.get("created_at")

        # Determine level:
        # Yellow if only 'drone' is reported; Red if 'missile', 'ballistic', or general
        if api_state == 1:
            all_causes = list(causes) if causes else ([last_cause] if last_cause else [])
            if all_causes and all(c == "drone" for c in all_causes):
                detected_level = "yellow"
            else:
                detected_level = "red"
        else:
            detected_level = None

        # Transition: 0 -> 1 (Alert Starts)
        if api_state == 1 and not self.is_alert:
            start_time_str = now.strftime("%H:%M")
            start_dt = now
            if created_at_raw:
                try:
                    c_dt = datetime.strptime(created_at_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KYIV_TZ)
                    start_time_str = c_dt.strftime("%H:%M")
                    start_dt = c_dt
                except Exception:
                    pass

            with self.lock:
                self.is_alert = True
                self.alert_level = detected_level
                self.alert_causes = causes
                self.alert_start_time = start_time_str
                self.alert_start_dt = start_dt
                self.alert_end_time = None
                self.alert_end_dt = None
                self.alert_duration = None
                self.lesson_resumes_at = None
                self.lesson_resume_dt = None
                self.resumed_banner_restored = False
                self._save_state()

            print(f"[AirAlert] NEW ALERT DETECTED in Kyiv: {detected_level.upper()} at {start_time_str} causes={causes}")
            self._broadcast_alert_start(detected_level, is_manual=False)

        # Transition: 1 -> 0 (Alert Ends / Відбій)
        elif api_state == 0 and self.is_alert:
            end_time_str = now.strftime("%H:%M")
            end_dt = now
            if created_at_raw:
                try:
                    c_dt = datetime.strptime(created_at_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KYIV_TZ)
                    end_time_str = c_dt.strftime("%H:%M")
                    end_dt = c_dt
                except Exception:
                    pass

            with self.lock:
                # Calculate duration
                if self.alert_start_dt:
                    delta_sec = max(0, (end_dt - self.alert_start_dt).total_seconds())
                    duration_min = max(1, int(round(delta_sec / 60.0)))
                elif self.alert_start_time:
                    try:
                        sp = [int(x) for x in self.alert_start_time.split(":")]
                        s_est = end_dt.replace(hour=sp[0], minute=sp[1], second=0)
                        if s_est > end_dt:
                            s_est -= timedelta(days=1)
                        duration_min = max(1, int(round((end_dt - s_est).total_seconds() / 60.0)))
                    except Exception:
                        duration_min = 15
                else:
                    duration_min = 15

                # Delay rule: <= 10 min -> 10 min; > 10 min -> 20 min
                delay_min = 10 if duration_min <= 10 else 20
                resume_dt = end_dt + timedelta(minutes=delay_min)
                resume_time_str = resume_dt.strftime("%H:%M")

                self.is_alert = False
                self.alert_level = "green"
                self.alert_end_time = end_time_str
                self.alert_end_dt = end_dt
                self.alert_duration = duration_min
                self.lesson_resumes_at = resume_time_str
                self.lesson_resume_dt = resume_dt
                self.resumed_banner_restored = False
                self._save_state()

            print(f"[AirAlert] ALL-CLEAR DETECTED in Kyiv: ended at {end_time_str}, duration={duration_min}m, resume={resume_time_str}")
            self._broadcast_alert_end(end_time_str, duration_min, resume_time_str, delay_min)

        # Update causes/level if changed while still active
        elif api_state == 1 and self.is_alert and detected_level != self.alert_level:
            with self.lock:
                self.alert_level = detected_level
                self.alert_causes = causes
                self._save_state()
            self._apply_stream_banner(alert_level=detected_level)

    def _broadcast_alert_start(self, level, is_manual=False):
        """Send chat alert and update video banner."""
        start_time = self.alert_start_time or get_kyiv_now().strftime("%H:%M")
        if level == "yellow":
            icon = "🟡"
            threat_name = "дронова небезпека"
        else:
            icon = "🔴"
            threat_name = "підвищена / ракетна небезпека"

        chat_msg = (
            f"{icon} УВАГА! Оголошено повітряну тривогу в м. Київ ({threat_name}). "
            f"Час початку: {start_time}. Перейдіть в укриття або безпечне місце!"
        )
        self._send_meet_chat(chat_msg)
        self._apply_stream_banner(alert_level=level, alert_time=start_time)

    def _broadcast_alert_end(self, end_time, duration_min, resume_time, delay_min):
        """Send chat all-clear and update video banner with resume time."""
        chat_msg = (
            f"🟢 Відбій повітряної тривоги в м. Київ. "
            f"Час відбою: {end_time} (тривала {duration_min} хв). "
            f"Заняття розпочнеться о {resume_time} (через {delay_min} хв)."
        )
        self._send_meet_chat(chat_msg)
        self._apply_stream_banner(
            alert_level="green",
            alert_time=end_time,
            alert_resumes_at=resume_time,
            alert_duration=duration_min,
        )

    def _send_meet_chat(self, text):
        """Dispatch chat message to Google Meet call if active."""
        if not self.send_chat_fn:
            return
        try:
            if self.is_in_meeting_fn and not self.is_in_meeting_fn():
                print(f"[AirAlert] Bot not currently in meeting. Skipping chat send: {text}")
                return
            self.send_chat_fn(text)
        except Exception as e:
            print(f"[AirAlert] Failed to send chat message: {e}")

    def _apply_stream_banner(self, alert_level=None, alert_time=None, alert_resumes_at=None, alert_duration=None):
        """Regenerate stream.y4m with alert parameters overlay and toggle camera."""
        if not self.apply_banner_fn:
            return
        try:
            base_banner = {}
            if self.get_banner_fn:
                base_banner = self.get_banner_fn() or {}

            cfg = dict(base_banner)
            cfg["alert_level"] = alert_level
            cfg["alert_time"] = alert_time
            cfg["alert_resumes_at"] = alert_resumes_at
            cfg["alert_duration"] = alert_duration

            self.apply_banner_fn(cfg)
        except Exception as e:
            print(f"[AirAlert] Failed to apply stream banner: {e}")
