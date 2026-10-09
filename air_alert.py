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
    def __init__(self, state_file=None, get_banner_fn=None, apply_banner_fn=None, send_chat_fn=None, is_in_meeting_fn=None, play_alarm_fn=None):
        self.state_file = state_file or "/app/air_alert_state.json"
        self.get_banner_fn = get_banner_fn
        self.apply_banner_fn = apply_banner_fn
        self.send_chat_fn = send_chat_fn
        self.is_in_meeting_fn = is_in_meeting_fn
        self.play_alarm_fn = play_alarm_fn

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

        # Meeting presence & announcement tracking
        self.bot_was_in_meeting = False
        self.consecutive_out_of_call = 0
        self.meeting_announced_state = None  # e.g. "alert_10:15_red" or "clear_10:20_10:40"

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
                    self.resumed_banner_restored = bool(data.get("resumed_banner_restored", False))

                    if data.get("alert_start_dt"):
                        try:
                            self.alert_start_dt = datetime.fromisoformat(data["alert_start_dt"])
                        except Exception:
                            pass
                    if data.get("alert_end_dt"):
                        try:
                            self.alert_end_dt = datetime.fromisoformat(data["alert_end_dt"])
                        except Exception:
                            pass
                    if data.get("lesson_resume_dt"):
                        try:
                            self.lesson_resume_dt = datetime.fromisoformat(data["lesson_resume_dt"])
                        except Exception:
                            pass

                    # Reconstruct datetimes if missing
                    now = get_kyiv_now()
                    if self.alert_end_time and not self.alert_end_dt:
                        try:
                            sp = [int(p) for p in self.alert_end_time.split(":")]
                            est = now.replace(hour=sp[0], minute=sp[1], second=0, microsecond=0)
                            if est > now:
                                est -= timedelta(days=1)
                            self.alert_end_dt = est
                        except Exception:
                            pass

                    if self.lesson_resumes_at and not self.lesson_resume_dt:
                        try:
                            sp = [int(p) for p in self.lesson_resumes_at.split(":")]
                            est = now.replace(hour=sp[0], minute=sp[1], second=0, microsecond=0)
                            if est < now and (now - est).total_seconds() > 3600 * 12:
                                est += timedelta(days=1)
                            self.lesson_resume_dt = est
                        except Exception:
                            pass
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
                "alert_start_dt": self.alert_start_dt.isoformat() if self.alert_start_dt else None,
                "alert_end_time": self.alert_end_time,
                "alert_end_dt": self.alert_end_dt.isoformat() if self.alert_end_dt else None,
                "alert_duration": self.alert_duration,
                "lesson_resumes_at": self.lesson_resumes_at,
                "lesson_resume_dt": self.lesson_resume_dt.isoformat() if self.lesson_resume_dt else None,
                "resumed_banner_restored": self.resumed_banner_restored,
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

    def on_meeting_joined(self):
        """Called when Google Meet streamer enters a call."""
        threading.Thread(target=self._handle_meeting_joined, daemon=True).start()

    def _handle_meeting_joined(self):
        """Handle streamer joining Google Meet: announce active alert or recent all-clear."""
        # Short pause to ensure Meet WebRTC stream and mic state are completely ready
        time.sleep(2.5)

        with self.lock:
            # Case 1: Active Alert
            if self.is_alert:
                lvl = self.alert_level or "red"
                start_time = self.alert_start_time or get_kyiv_now().strftime("%H:%M")
                target_state = f"alert_{start_time}_{lvl}"
                if self.meeting_announced_state == target_state:
                    print(f"[AirAlert] Active alert {target_state} already announced in current meeting. Skipping.")
                    return
                self.meeting_announced_state = target_state
                print(f"[AirAlert] Meet bot joined during ACTIVE ALERT ({lvl}, {start_time}). Broadcasting to meeting...")
                threading.Thread(target=self._broadcast_alert_start, args=(lvl,), daemon=True).start()
                return

            # Case 2: Recent All-Clear (10-20 min ago / resume delay active)
            now = get_kyiv_now()
            mins_since_end = None
            if self.alert_end_dt:
                mins_since_end = max(0, (now - self.alert_end_dt).total_seconds() / 60.0)

            is_within_resume = bool(
                self.lesson_resume_dt and now < self.lesson_resume_dt and not self.resumed_banner_restored
            )
            is_recent_clear = bool(
                mins_since_end is not None and mins_since_end <= 20 and not self.resumed_banner_restored
            )

            if (is_within_resume or is_recent_clear) and self.alert_end_time and self.lesson_resumes_at:
                target_state = f"clear_{self.alert_end_time}_{self.lesson_resumes_at}"
                if self.meeting_announced_state == target_state:
                    print(f"[AirAlert] Recent clear {target_state} already announced in current meeting. Skipping.")
                    return
                self.meeting_announced_state = target_state
                duration = self.alert_duration or 15
                delay_min = 10 if duration <= 10 else 20
                print(f"[AirAlert] Meet bot joined during RECENT ALL-CLEAR (ended {self.alert_end_time}, resume {self.lesson_resumes_at}). Broadcasting to meeting...")
                threading.Thread(
                    target=self._broadcast_alert_end,
                    args=(self.alert_end_time, duration, self.lesson_resumes_at, delay_min),
                    daemon=True
                ).start()

    def _worker_loop(self):
        # Immediate initial check on startup
        if self.monitoring_enabled and not self.manual_override:
            try:
                self._check_api()
            except Exception as e:
                print(f"[AirAlert] Initial API check error: {e}")

        while not self._stop_event.is_set():
            try:
                now = get_kyiv_now()

                # 1. Track Google Meet presence
                if self.is_in_meeting_fn:
                    in_call = None
                    try:
                        in_call = self.is_in_meeting_fn()
                    except Exception:
                        pass

                    if in_call is True:
                        self.consecutive_out_of_call = 0
                        if not self.bot_was_in_meeting:
                            print("[AirAlert] Meet streamer joined meeting (detected in polling loop).", flush=True)
                            self.bot_was_in_meeting = True
                            self.on_meeting_joined()
                    elif in_call is False:
                        self.consecutive_out_of_call += 1
                        # Require at least 3 consecutive confirmed negative checks (>=15s) to avoid false resets
                        if self.consecutive_out_of_call >= 3 and self.bot_was_in_meeting:
                            print("[AirAlert] Meet streamer confirmed left meeting.", flush=True)
                            self.bot_was_in_meeting = False
                            self.meeting_announced_state = None
                    # in_call is None means timeout/busy: retain current presence state unchanged

                # 2. Check if lesson resumption time reached -> auto restore banner
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
                        self._send_meet_chat("🔔 Синхронне навчання продовжується!")
                        self._play_alarm_sound("resume")

                # 3. Check Kyiv Digital API
                if self.monitoring_enabled and not self.manual_override:
                    self._check_api()

            except Exception as e:
                print(f"[AirAlert] Worker loop error: {e}")

            # Sleep 5 seconds before next check
            self._stop_event.wait(5)

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
        with self.lock:
            self.meeting_announced_state = f"alert_{start_time}_{level}"

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
        self._play_alarm_sound("on")

    def _broadcast_alert_end(self, end_time, duration_min, resume_time, delay_min):
        """Send chat all-clear and update video banner with resume time."""
        with self.lock:
            self.meeting_announced_state = f"clear_{end_time}_{resume_time}"

        chat_msg = (
            f"🟢 Відбій повітряної тривоги в м. Київ. "
            f"Час відбою: {end_time} (тривала {duration_min} хв). "
            f"Синхронне навчання відновиться о {resume_time} (через {delay_min} хв)."
        )
        self._send_meet_chat(chat_msg)
        self._apply_stream_banner(
            alert_level="green",
            alert_time=end_time,
            alert_resumes_at=resume_time,
            alert_duration=duration_min,
        )
        self._play_alarm_sound("off")

    def _play_alarm_sound(self, sound_type):
        """Play alarm on/off sound in Google Meet call asynchronously."""
        if not self.play_alarm_fn:
            return
        try:
            if self.is_in_meeting_fn and not self.is_in_meeting_fn():
                print(f"[AirAlert] Bot not currently in meeting. Skipping alarm sound: {sound_type}")
                return
            threading.Thread(target=self.play_alarm_fn, args=(sound_type,), daemon=True).start()
        except Exception as e:
            print(f"[AirAlert] Failed to trigger alarm sound: {e}")

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
