

import cv2
import numpy as np
import time
import os
import threading
import requests
from collections import deque

TELEGRAM_BOT_TOKEN = "8683310890:AAEY0CaQrmGIC8CB_HbT1mnjLtulxEe4hYI"
TELEGRAM_CHAT_ID   = "1669940496"


HISTORY_LEN  = 6
fire_history = deque(maxlen=HISTORY_LEN)


fire_was_active = False
alert_count     = 0


water_approval_status = None  
last_offset           = 0     

ALARM_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alarm-sound.mp3")
alarm_playing = False
alarm_thread  = None


def play_alarm():
    global alarm_playing
    try:
        import pygame
        pygame.mixer.init()
        pygame.mixer.music.load(ALARM_FILE)
        pygame.mixer.music.play(-1)
        while alarm_playing:
            time.sleep(0.1)
        pygame.mixer.music.stop()
        pygame.mixer.quit()
    except Exception as e:
        print(f"[Audio Error] {e}")


def start_alarm():
    global alarm_playing, alarm_thread
    if not alarm_playing:
        alarm_playing = True
        alarm_thread = threading.Thread(target=play_alarm, daemon=True)
        alarm_thread.start()


def stop_alarm():
    global alarm_playing
    alarm_playing = False


def send_telegram_alert(count, frame):
    """Send fire alert text + photo + YES/NO approval buttons."""
    snapshot_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"fire_snapshot_{count}.jpg"
    )
    cv2.imwrite(snapshot_path, frame)

    def _send():
        global water_approval_status
        water_approval_status = None  # reset for new event

        try:
           
            url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id"   : TELEGRAM_CHAT_ID,
                "parse_mode": "HTML",
                "text"      : (
                    f"🚨 <b>FIRE ALERT #{count}</b> 🚨\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>User:</b> Sagar\n"
                    f"🕐 <b>Time:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"📍 <b>Location:</b> Camera Feed\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔥 <b>Fire detected by your camera!</b>\n\n"
                    f"⚠️ <b>Immediate action required!</b>\n"
                    f"📞 Call emergency: <b>112</b>\n\n"
                    f"💧 <b>Approve water sprinkler system?</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                ),
                "reply_markup": {
                    "inline_keyboard": [[
                        {
                            "text"         : "✅ YES — Spray Water",
                            "callback_data": f"SPRAY_YES_{count}"
                        },
                        {
                            "text"         : "❌ NO — Cancel",
                            "callback_data": f"SPRAY_NO_{count}"
                        }
                    ]]
                }
            }
            requests.post(url, json=payload, timeout=10)

           
            photo_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            with open(snapshot_path, "rb") as photo_file:
                files    = {"photo": photo_file}
                data     = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": f"📸 <b>Fire Snapshot #{count}</b>\n🕐 {time.strftime('%H:%M:%S')}",
                    "parse_mode": "HTML"
                }
                response = requests.post(photo_url, data=data, files=files, timeout=10)

            if response.status_code == 200:
                print(f"[Telegram] Alert #{count} + photo sent!")
            else:
                print(f"[Telegram Error] {response.text}")

        except Exception as e:
            print(f"[Telegram Error] {e}")
        finally:
            if os.path.exists(snapshot_path):
                os.remove(snapshot_path)

    threading.Thread(target=_send, daemon=True).start()


def send_confirmation(text):
    """Send a confirmation message after user taps YES or NO."""
    try:
        url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id"   : TELEGRAM_CHAT_ID,
            "text"      : text,
            "parse_mode": "HTML"
        }
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Telegram Error] {e}")


def poll_telegram_responses():
    """Background thread: polls Telegram for YES/NO button responses."""
    global last_offset, water_approval_status
    while True:
        try:
            url    = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": last_offset, "timeout": 5}
            resp   = requests.get(url, params=params, timeout=10)
            data   = resp.json()

            for update in data.get("result", []):
                last_offset = update["update_id"] + 1

                # Handle inline button callback
                if "callback_query" in update:
                    cb      = update["callback_query"]
                    cb_data = cb.get("data", "")
                    cb_id   = cb["id"]

                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                        json={"callback_query_id": cb_id},
                        timeout=5
                    )

                    if cb_data.startswith("SPRAY_YES"):
                        water_approval_status = "approved"
                        print("[Telegram] ✅ Water spray APPROVED by Sagar!")
                        send_confirmation(
                            "✅ <b>Water Spray APPROVED!</b>\n"
                            "💧 Activating sprinkler system now...\n"
                            "🚒 Please also evacuate and call 112!"
                        )

                    elif cb_data.startswith("SPRAY_NO"):
                        water_approval_status = "denied"
                        print("[Telegram] ❌ Water spray DENIED by Sagar.")
                        send_confirmation(
                            "❌ <b>Water Spray Cancelled.</b>\n"
                            "⚠️ Fire is still active!\n"
                            "🚒 Please evacuate immediately and call 112!"
                        )

        except Exception:
            pass
        time.sleep(1)




def is_fire_region(roi):
    if roi.size == 0:
        return False
    hsv_roi  = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    h, w     = hsv_roi.shape[:2]
    mean_sat = np.mean(hsv_roi[:, :, 1])
    mean_val = np.mean(hsv_roi[:, :, 2])
    if mean_sat < 140 or mean_val < 140:
        return False
    top_val = np.mean(hsv_roi[:h//2, :, 2])
    bot_val = np.mean(hsv_roi[h//2:, :, 2])
    if bot_val > top_val + 40:
        return False
    return True


def detect_fire(frame, circle_radius_offset):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    lower_red1   = np.array([0,   160, 160])
    upper_red1   = np.array([8,   255, 255])
    lower_red2   = np.array([172, 160, 160])
    upper_red2   = np.array([180, 255, 255])
    lower_orange = np.array([8,   180, 180])
    upper_orange = np.array([22,  255, 255])
    lower_yellow = np.array([22,  160, 200])
    upper_yellow = np.array([30,  255, 255])

    mask_r1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask_r2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask_o  = cv2.inRange(hsv, lower_orange, upper_orange)
    mask_y  = cv2.inRange(hsv, lower_yellow, upper_yellow)

    fire_mask = cv2.bitwise_or(mask_r1, mask_r2)
    fire_mask = cv2.bitwise_or(fire_mask, mask_o)
    fire_mask = cv2.bitwise_or(fire_mask, mask_y)

    kernel    = np.ones((7, 7), np.uint8)
    fire_mask = cv2.erode(fire_mask, kernel, iterations=2)
    fire_mask = cv2.dilate(fire_mask, kernel, iterations=3)

    contours, _ = cv2.findContours(fire_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    fire_detected = False

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 1500:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if w / float(h) > 2.5:
            continue
        roi = frame[y:y+h, x:x+w]
        if not is_fire_region(roi):
            continue

        fire_detected = True
        cx     = x + w // 2
        cy     = y + h // 2
        radius = max(w, h) // 2 + 20 + circle_radius_offset

        cv2.circle(frame, (cx, cy), radius,     (0, 0, 255), 3)
        cv2.circle(frame, (cx, cy), radius - 8, (0, 80, 255), 1)
        cv2.drawMarker(frame, (cx, cy), (0, 0, 255), cv2.MARKER_CROSS, 20, 2)
        cv2.putText(frame, "FIRE!", (cx - 32, cy - radius - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)

    fire_history.append(1 if fire_detected else 0)
    return frame, fire_mask, sum(fire_history) >= 3


def draw_alert_banner(frame, blink_state, count, approval):
    h, w  = frame.shape[:2]
    color = (0, 0, 220) if blink_state else (0, 0, 150)
    cv2.rectangle(frame, (0, 0), (w, 65), color, -1)
    cv2.putText(frame, "  FIRE ALERT! EVACUATE IMMEDIATELY!", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.putText(frame, f"  Alert #{count} sent | Check Telegram to approve sprinkler", (10, 58),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 220, 0), 1)
    border_color = (0, 0, 255) if blink_state else (0, 0, 160)
    cv2.rectangle(frame, (0, 0), (w - 1, h - 1), border_color, 6)

    if approval == "approved":
        bar_color = (0, 180, 0)
        status_text = "  💧 WATER SPRAY: APPROVED & ACTIVATED"
    elif approval == "denied":
        bar_color = (0, 100, 200)
        status_text = "  ❌ WATER SPRAY: DENIED — Call 112 Now!"
    else:
        bar_color = (0, 80, 180)
        status_text = "  💧 Awaiting water spray approval on Telegram..."

    cv2.rectangle(frame, (0, h - 50), (w, h), bar_color, -1)
    cv2.putText(frame, status_text, (10, h - 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    return frame


def draw_safe_banner(frame, count):
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w, 50), (0, 130, 0), -1)
    cv2.putText(frame, f"  ✅ Monitoring... No Fire Detected | Alerts Sent: {count}", (10, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return frame


def main():
    global fire_was_active, alert_count, water_approval_status

    if not os.path.exists(ALARM_FILE):
        print(f"[Warning] alarm-sound.mp3 not found in script folder.")


    poll_thread = threading.Thread(target=poll_telegram_responses, daemon=True)
    poll_thread.start()
    print("[Telegram] Polling for responses started...")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not open camera.")
        return

    print("Fire Detection Started. Press 'q' to quit.")

    blink_state = False
    blink_timer = time.time()
    pulse       = 0
    pulse_dir   = 1

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        pulse += pulse_dir * 2
        if pulse >= 15 or pulse <= 0:
            pulse_dir *= -1

        if time.time() - blink_timer > 0.4:
            blink_state = not blink_state
            blink_timer = time.time()

        output_frame, fire_mask, fire_confirmed = detect_fire(frame, pulse)

        if fire_confirmed:
            if not fire_was_active:
                alert_count          += 1
                fire_was_active       = True
                water_approval_status = None
                start_alarm()
                send_telegram_alert(alert_count, output_frame.copy())

            output_frame = draw_alert_banner(
                output_frame, blink_state, alert_count, water_approval_status
            )
        else:
            if fire_was_active:
                fire_was_active = False
                stop_alarm()
            output_frame = draw_safe_banner(output_frame, alert_count)

        cv2.imshow("🔥 Fire Detection System — Sagar", output_frame)
        cv2.imshow("Fire Mask", fire_mask)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    stop_alarm()
    cap.release()
    cv2.destroyAllWindows()
    print(f"System stopped. Total alerts sent: {alert_count}")


if __name__ == "__main__":
    main()