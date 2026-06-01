# Tool Prompt Spec — 7 công cụ của ReAct Agent (IT4210)

> Đặc tả prompt cho từng tool: **mục đích · khi nào dùng · định dạng tham số ·
> ví dụ gọi · kết quả mẫu**. Mọi kết quả mẫu của 5 tool local là **output THẬT**
> chạy từ `src/tools/` trên `data/embedded_labs.json` (không bịa).
>
> Định dạng gọi tool trong vòng ReAct: một dòng `Action: <tên_tool>(<tham_số>)`.
> Agent chỉ truyền **một chuỗi** vào tool; tool trả về một chuỗi (Observation).
> Đăng ký 7 tool: `src/tools/__init__.py`. Logic: `lab_tools.py`, `web_tools.py`.

---

## 1. `get_lab_objective`

- **Mục đích**: Lấy *mục đích / mục tiêu* của một bài lab.
- **Khi nào dùng**: người dùng hỏi "Lab X để làm gì / mục tiêu / học được gì".
- **Tham số**: số bài — `1`, `2`, hoặc `3`.
- **LLM description (prompt sống)**: *"Lấy mục đích/mục tiêu của một bài lab Hệ
  nhúng. Tham số: số bài (1, 2 hoặc 3)."*
- **Ví dụ gọi**: `Action: get_lab_objective(1)`
- **Kết quả mẫu (thật)**:
```
Bài thực hành 1 - GHÉP NỐI VỚI GPIO, INTERRUPT, TIMER
Mục đích:
- Củng cố kiến thức về các ngoại vi cơ bản: GPIO, ngắt ngoài, timer.
- Thực hành ghép nối với LED đơn và LED 7 thanh.
- Tìm hiểu chuẩn NEC Protocol trong điều khiển hồng ngoại; kết hợp ngắt ngoài + timer để giải mã lệnh.
- Xây dựng ứng dụng đơn giản với LED 7 thanh và remote control.
```

---

## 2. `get_lab_preparation`

- **Mục đích**: Lấy phần *chuẩn bị* — phần cứng, phần mềm, tài liệu.
- **Khi nào dùng**: "Lab X cần chuẩn bị gì / cần linh kiện/phần mềm/tài liệu nào".
- **Tham số**: số bài — `1`, `2`, hoặc `3`.
- **LLM description**: *"Lấy phần chuẩn bị (phần cứng, phần mềm, tài liệu) của một
  lab. Tham số: số bài (1, 2 hoặc 3)."*
- **Ví dụ gọi**: `Action: get_lab_preparation(2)`
- **Kết quả mẫu (thật)**:
```
Bài thực hành 2 - GHÉP NỐI NỐI TIẾP (I2C, SPI)
Phần cứng:
  - Bộ kit STM32F429I
  - Module Tiny RTC (DS1307 + AT24C32)
  - Màn hình OLED SH1106 1.3 inch
  - Module RFID RC522 và thẻ RFID 13.56 MHz
Phần mềm: STM32CubeIDE, Hercules
Tài liệu:
  - Datasheet DS1307, AT24C32
  - Mã nguồn thư viện sh1106.*, fonts.*, tm_stm32f4_mfrc522.*
```

---

## 3. `get_exercise_guide`

- **Mục đích**: Lấy *hướng dẫn các phần + danh sách bài tập* của một lab, có thể lọc
  theo chủ đề.
- **Khi nào dùng**: "Hướng dẫn bài tập Lab X", "Lab 2 phần RFID làm gì".
- **Tham số**: `số bài` **hoặc** `số bài + từ khóa chủ đề`, vd `2 rfid`, `1 led`.
  (Không có chủ đề → trả toàn bộ các phần.)
- **LLM description**: *"Lấy hướng dẫn các phần và bài tập của một lab. Tham số: số
  bài, có thể kèm chủ đề, vd '2 rfid'."*
- **Ví dụ gọi**: `Action: get_exercise_guide(2 rfid)`
- **Kết quả mẫu (thật, rút gọn)**:
```
Bài thực hành 2 - GHÉP NỐI NỐI TIẾP (I2C, SPI)
Các phần hướng dẫn:
  [3.3] Tìm hiểu module RC522 RFID reader: Đọc/ghi thẻ 13.56 MHz. Module hàn cứng chỉ dùng SPI.
Bài tập:
  - Viết lại TestDS1307() thành SetTime()/GetTime().
  - Hiển thị mã thẻ (5 byte) đọc được từ RC522 lên SH1106.
  - (3.9) RFID quẹt → LED3 bật; mã khớp → LED4 + 'Welcome' + lưu log; không khớp → 'Rejected'.
```

---

## 4. `search_lab_docs`

- **Mục đích**: *Tìm kiếm toàn văn* theo từ khóa trên cả 3 lab, **không phân biệt
  dấu** (vd "ngat" khớp "ngắt").
- **Khi nào dùng**: câu hỏi không gắn rõ số bài, hoặc cần tìm khái niệm/keyword khắp
  tài liệu ("ở đâu nói về NEC", "ngắt ngoài nằm bài nào").
- **Tham số**: từ khóa (chuỗi tự do).
- **LLM description**: *"Tìm kiếm theo từ khóa trong toàn bộ tài liệu 3 lab (không
  phân biệt dấu). Tham số: từ khóa."*
- **Ví dụ gọi**: `Action: search_lab_docs(ngat)`
- **Kết quả mẫu (thật, rút gọn)**:
```
Kết quả cho 'ngat':
- (Lab 1 | Mục đích) Củng cố kiến thức GPIO, ngắt ngoài (external interrupt), timer.
- (Lab 1 | 3.0 Project mẫu) PA0 cấu hình External interrupt + Rising edge; Timer 6 sinh ngắt 10000 Hz...
- (Lab 1 | 3.3 NEC) HS0038 Out→PG5; dùng ngắt ngoài EXTI5 + timer để đo độ rộng bit.
```
- **Không khớp** → `Không tìm thấy nội dung nào khớp với '<từ khóa>'.`

---

## 5. `lookup_pin_mapping`

- **Mục đích**: Tra *sơ đồ chân / ghép nối* theo **số bài** hoặc **tên linh kiện**.
- **Khi nào dùng**: "Sơ đồ chân RC522", "Lab 1 nối chân nào", "HS0038 cắm đâu".
- **Tham số**: `1/2/3` **hoặc** tên linh kiện (`rc522`, `led`, `hs0038`, `ds1307`...).
- **LLM description**: *"Tra cứu sơ đồ chân/ghép nối của một lab hoặc linh kiện
  (rc522, led, hs0038, ds1307...). Tham số: số bài hoặc tên linh kiện."*
- **Ví dụ gọi**: `Action: lookup_pin_mapping(rc522)`
- **Kết quả mẫu (thật)**:
```
Sơ đồ chân tìm được:
- (Lab 2) SPI4 RC522 - SS: PE4 (GPIO_Output)
- (Lab 2) SPI4 RC522 - SCK: PE2
- (Lab 2) SPI4 RC522 - MISO: PE5
- (Lab 2) SPI4 RC522 - MOSI: PE6
- (Lab 2) RC522 - RST: 3V
- (Lab 2) Nguồn: SH1106→3V; TinyRTC BAT→3V, VCC→5V; RC522 VCC→3V + tụ 100uF
```
- **Không khớp** → gợi ý: `Thử: 1/2/3, rc522, led, hs0038, ds1307...`

---

## 6. `web_search` *(bonus — cần mạng)*

- **Mục đích**: Tìm trên Internet (DuckDuckGo) khi câu hỏi **nằm ngoài** tài liệu
  lab (datasheet, chi tiết chuẩn giao tiếp, HAL...).
- **Khi nào dùng**: 5 tool trên không có dữ liệu; cần nguồn ngoài.
- **Tham số**: câu truy vấn.
- **LLM description**: *"Tìm kiếm trên Internet khi câu hỏi nằm ngoài tài liệu lab
  (vd datasheet, chuẩn giao tiếp). Tham số: câu truy vấn."*
- **Ví dụ gọi**: `Action: web_search(DS1307 datasheet I2C address)`
- **Kết quả mẫu (định dạng)**:
```
Kết quả web cho 'DS1307 datasheet I2C address':
- <tiêu đề> (<url>)
  <trích đoạn ~200 ký tự>
- ...
```
- **Xuống cấp êm**: chưa cài thư viện → nhắc `pip install ddgs`; offline/lỗi mạng →
  `Lỗi tìm kiếm (...). Có thể đang offline.` (agent vẫn dùng được tool local).

---

## 7. `fetch_url` *(bonus — cần mạng)*

- **Mục đích**: Tải *nội dung văn bản* của một URL (đã lược HTML) để đọc trực tiếp.
- **Khi nào dùng**: đã có URL (vd từ `web_search`) và cần lấy nội dung chi tiết.
- **Tham số**: địa chỉ URL (phải bắt đầu `http://` hoặc `https://`).
- **LLM description**: *"Tải nội dung văn bản của một URL. Tham số: địa chỉ URL."*
- **Ví dụ gọi**: `Action: fetch_url(https://www.st.com/resource/en/datasheet/ds1307.pdf)`
- **Kết quả mẫu (định dạng)**: văn bản đã lược thẻ HTML, cắt tối đa ~2000 ký tự
  (kèm `...` nếu dài hơn).
- **Bảo vệ**: URL không hợp lệ → `URL không hợp lệ (phải bắt đầu bằng http:// hoặc
  https://).`; tải lỗi → `Không tải được <url> (...).`

---

## Quy tắc prompt chung (đã có trong system prompt — `agent.py:65-91`)

1. Mỗi lượt **chỉ** xuất một khối `Thought:` + `Action: tool(args)`, rồi **DỪNG**
   chờ `Observation` (không tự bịa Observation).
2. Chỉ dùng tool có tên trong danh sách; tham số trong ngoặc đơn, không nháy thừa.
3. Khi đủ thông tin → kết thúc bằng `Final Answer: <trả lời tiếng Việt>`.
4. Câu đơn giản, đã rõ → có thể trả `Final Answer` ngay, không cần gọi tool.

### Gợi ý chọn tool theo ý định người dùng
| Người dùng hỏi về... | Tool nên gọi |
| :--- | :--- |
| mục đích/mục tiêu bài | `get_lab_objective` |
| cần chuẩn bị gì | `get_lab_preparation` |
| hướng dẫn/bài tập (có thể theo chủ đề) | `get_exercise_guide` |
| khái niệm/keyword không rõ bài nào | `search_lab_docs` |
| sơ đồ chân/ghép nối | `lookup_pin_mapping` |
| kiến thức ngoài tài liệu lab | `web_search` → `fetch_url` |
