from __future__ import annotations

from io import BytesIO
import json
import unittest
from unittest.mock import patch

from PIL import Image

import bot
from notification_cards import (
    notification_caption,
    parse_card_content,
    parse_hourly_rows,
    render_notification_card,
)


HOURLY_TEXT = """🟡 資金異動

幣種：AAAUSDT
階段：回踩進場
決策：可分批做多
總分：82/100｜品質：A級｜風險：低
流動性：合格 88/100｜24H $120.00M｜0.5%深度 B/A $80K/$70K
理由：日線長底部 / 價格與 OI 同步
進場條件：回踩守穩，可分批

-----

🟡 資金異動

幣種：PEOPLEUSDT
階段：失效/派發
決策：不要進
總分：35/100｜品質：C級｜風險：高
流動性：不足 51/100｜24H $2.29M｜0.5%深度 B/A $27K/$26K
理由：流動性不足：24H成交額低於門檻
進場條件：目前不進場

摘要：掃描 50｜可開 1｜待確認 0｜不開 1
"""


EVENT_TEXT = """1H 資金點火｜SKLUSDT
類型：底部點火
價格 1H：+6.10%｜合約 OI 1H：+5.40%
結構：84/100｜Funding：+0.0050%｜OI $12.40M
24H成交額：$390.85M｜判定：再確認：等待 5-15 分鐘回踩守住
"""


class NotificationCardTests(unittest.TestCase):
    def test_hourly_report_is_condensed_into_one_ranked_card(self) -> None:
        rows = parse_hourly_rows(HOURLY_TEXT)
        self.assertEqual([row.symbol for row in rows], ["AAAUSDT", "PEOPLEUSDT"])
        self.assertEqual(rows[1].decision, "不要進")
        image_bytes = render_notification_card(HOURLY_TEXT)
        with Image.open(BytesIO(image_bytes)) as image:
            self.assertEqual(image.width, 1200)
            self.assertGreater(image.height, 400)
            self.assertEqual(image.format, "PNG")

    def test_event_card_keeps_only_action_metrics_and_reason(self) -> None:
        content = parse_card_content(EVENT_TEXT)
        self.assertEqual(content.symbol, "SKLUSDT")
        self.assertEqual(content.decision, "再確認")
        self.assertIn(("1H 價格", "+6.10%"), content.metrics)
        self.assertIn(("1H OI", "+5.40%"), content.metrics)
        self.assertIn("等待 5-15 分鐘", content.action)
        image_bytes = render_notification_card(EVENT_TEXT)
        with Image.open(BytesIO(image_bytes)) as image:
            self.assertEqual(image.size, (1200, 820))

    def test_caption_is_short_and_actionable(self) -> None:
        caption = notification_caption(EVENT_TEXT)
        self.assertIn("SKLUSDT", caption)
        self.assertIn("再確認", caption)
        self.assertLessEqual(len(caption), 180)


class CardDeliveryTests(unittest.TestCase):
    def test_telegram_send_photo_builds_multipart_request(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"ok": True, "result": {"message_id": 1}}).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=Response()) as urlopen:
            result = bot.telegram_send_photo("token", 123, b"png-bytes", caption="caption")
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/bottoken/sendPhoto"))
        self.assertIn("multipart/form-data", request.headers["Content-type"])
        self.assertIn(b"png-bytes", request.data)
        self.assertEqual(result["message_id"], 1)

    def test_send_card_message_uses_send_photo(self) -> None:
        with (
            patch.object(bot, "render_notification_card", return_value=b"png"),
            patch.object(bot, "notification_caption", return_value="caption"),
            patch.object(bot, "telegram_send_photo") as send_photo,
        ):
            bot.send_card_message("token", 123, EVENT_TEXT)
        send_photo.assert_called_once_with("token", 123, b"png", caption="caption")

    def test_send_card_message_falls_back_to_text(self) -> None:
        with (
            patch.object(bot, "render_notification_card", side_effect=RuntimeError("render failed")),
            patch.object(bot, "send_long_message") as send_text,
            patch("builtins.print"),
        ):
            bot.send_card_message("token", 123, EVENT_TEXT)
        send_text.assert_called_once_with("token", 123, EVENT_TEXT)


if __name__ == "__main__":
    unittest.main()
