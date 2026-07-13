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
方向：做多
信心：86/100｜多分 86｜空分 20
階段：回踩進場｜模型總分 82/100｜品質 A級｜風險 低
流動性：合格 88/100｜24H $120.00M｜0.5%深度 B/A $80K/$70K
進場區($)：0.098 - 0.101
TP1($)：0.108｜報酬 8.00%｜RR 1.60
TP2($)：0.115｜報酬 15.00%｜RR 3.00
SL($)：0.095｜風險 5.00%
理由：日線長底部 / 價格與 OI 同步
倉位管理：TP1停利一半，剩餘止損移到進場均價

-----

🟡 資金異動

幣種：PEOPLEUSDT
方向：不交易
信心：35/100｜多分 35｜空分 20
階段：失效/派發｜模型總分 35/100｜品質 C級｜風險 高
流動性：不足 51/100｜24H $2.29M｜0.5%深度 B/A $27K/$26K
進場區($)：不建立倉位
TP1($)：-
TP2($)：-
SL($)：-
理由：流動性不足：24H成交額低於門檻

-----

🟡 資金異動

幣種：SHORTUSDT
方向：做空
信心：81/100｜多分 22｜空分 81
階段：失效/派發｜模型總分 79/100｜品質 A級｜風險 中
流動性：合格 91/100｜24H $240.00M｜0.5%深度 B/A $120K/$140K
進場區($)：0.990 - 1.010
TP1($)：0.925｜報酬 7.50%｜RR 1.50
TP2($)：0.875｜報酬 12.50%｜RR 2.50
SL($)：1.050｜風險 5.00%
理由：高位轉弱且價格下跌/OI增加，空方資金確認
倉位管理：TP1停利一半，剩餘止損移到進場均價

摘要：掃描 50｜做多 1｜做空 1｜不交易 1
"""


EVENT_TEXT = """交易計畫｜SKLUSDT
方向：做多｜信心：84/100
進場區($)：0.098 - 0.101
TP1($)：0.108｜RR 1.60
TP2($)：0.115｜RR 3.00
SL($)：0.095｜風險 5.00%
理由：4H回收且價格/OI同步，底部與資金條件通過
倉位管理：TP1停利一半，剩餘止損移到進場均價；TP2出清剩餘
"""


class NotificationCardTests(unittest.TestCase):
    def test_hourly_report_is_condensed_into_one_ranked_card(self) -> None:
        rows = parse_hourly_rows(HOURLY_TEXT)
        self.assertEqual([row.symbol for row in rows], ["AAAUSDT", "PEOPLEUSDT", "SHORTUSDT"])
        self.assertEqual(rows[0].decision, "做多")
        self.assertEqual(rows[0].tp1, "0.108")
        self.assertEqual(rows[1].decision, "不交易")
        self.assertEqual(rows[2].decision, "做空")
        image_bytes = render_notification_card(HOURLY_TEXT)
        with Image.open(BytesIO(image_bytes)) as image:
            self.assertEqual(image.width, 1200)
            self.assertGreater(image.height, 400)
            self.assertEqual(image.format, "PNG")

    def test_event_card_keeps_only_action_metrics_and_reason(self) -> None:
        content = parse_card_content(EVENT_TEXT)
        self.assertEqual(content.symbol, "SKLUSDT")
        self.assertEqual(content.decision, "做多")
        self.assertIn(("進場區", "0.098 - 0.101"), content.metrics)
        self.assertIn(("TP1", "0.108"), content.metrics)
        self.assertIn("TP1停利一半", content.action)
        image_bytes = render_notification_card(EVENT_TEXT)
        with Image.open(BytesIO(image_bytes)) as image:
            self.assertEqual(image.size, (1200, 820))

    def test_caption_is_short_and_actionable(self) -> None:
        caption = notification_caption(EVENT_TEXT)
        self.assertIn("SKLUSDT", caption)
        self.assertIn("做多", caption)
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
