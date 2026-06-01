---
name: lab-assistant
description: Trợ lý hỗ trợ lab môn Hệ nhúng (IT4210). Dùng khi người dùng hỏi về mục đích lab, chuẩn bị lab, sơ đồ chân, hoặc hướng dẫn bài tập của Lab 1/2/3 (GPIO/Interrupt/Timer, I2C/SPI, FreeRTOS/TouchGFX). Cũng dùng để chạy chatbot/ReAct agent của dự án.
---

# Lab Assistant — môn Hệ nhúng IT4210

Trợ lý này trả lời về 3 bài thực hành (tài liệu gốc trong `docs/`):

- **Lab 1** — Ghép nối với GPIO, Interrupt, Timer (LED đơn, LED 7 thanh, NEC remote).
- **Lab 2** — Ghép nối nối tiếp I2C/SPI (DS1307 RTC, OLED SH1106, RC522 RFID).
- **Lab 3** — FreeRTOS và TouchGFX (đa nhiệm + giao diện đồ họa).

## Khi nào dùng skill này
Người dùng hỏi về: mục đích/mục tiêu một lab, cần chuẩn bị phần cứng/phần mềm gì,
sơ đồ chân ghép nối (vd RC522, HS0038, LED), hoặc các bước/bài tập của một lab.

## Cách trả lời
1. Ưu tiên dữ liệu trong `data/embedded_labs.json` (đã trích từ PDF). Đừng bịa số chân.
2. Cấu trúc câu trả lời theo: **Mục đích → Chuẩn bị → Hướng dẫn/Bài tập**.
3. Nếu hỏi nội dung ngoài tài liệu (datasheet, chuẩn giao tiếp), dùng MCP `fetch`
   hoặc `context7` để tra trên mạng rồi trích dẫn nguồn.

## Chạy ứng dụng (chatbot vs agent)
```bash
python main.py agent --once "Lab 2 cần chuẩn bị gì?"   # ReAct agent + tools
python main.py chatbot --once "Mục đích Lab 1 là gì?"  # baseline (không tool)
python main.py compare "Sơ đồ chân RC522?"            # so sánh 2 cách
```

## Tham chiếu nhanh sơ đồ chân
- LED đơn: PD8–PD15 · LED 7 thanh: PE8–PE15 (thanh), PG2/PG3 (chọn module)
- HS0038 (IR): Out→PG5 (EXTI5) · Nút B1: PA0
- RC522 (SPI4): SS→PE4, SCK→PE2, MISO→PE5, MOSI→PE6, RST→3V · DS1307 I2C: 0xD0/0xD1
- Lab 3 (FreeRTOS): LED PG13/PG14, USER_BUTTON PA0, UART USART1 115200
