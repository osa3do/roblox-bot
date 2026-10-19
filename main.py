import csv
import io
import re
import zipfile
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# التوكن الخاص بك مدمج مباشرة
TOKEN = "8693857710:AAGITvtrmfQ7q5NE_0anHS14ue6ENRp2irE"


def parse_csv_text(text):
  if not text:
    return []
  lines = text.splitlines()
  result = []
  for line in lines:
    reader = csv.reader(io.StringIO(line))
    for row in reader:
      result.append([item.strip() for item in row])
  return result


def analyze_roblox_zip(zip_bytes):
  extracted_files = {}
  try:
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as z:
      for filename in z.namelist():
        lower_name = filename.lower()
        if lower_name.endswith((".csv", ".txt")):
          with z.open(filename) as f:
            content = f.read().decode("utf-8", errors="ignore")
            clean_name = filename.split("/")[-1].lower()
            extracted_files[clean_name] = content
  except Exception as e:
    return None, f"خطأ في قراءة ملف الـ ZIP: {e}"

  def get_file_content(keywords):
    for fname, content in extracted_files.items():
      if all(kw in fname for kw in keywords):
        return content
    for fname, content in extracted_files.items():
      if any(kw in fname for kw in keywords):
        return content
    return None

  def get_user_content():
    for fname, content in extracted_files.items():
      if (
          "user" in fname
          and "device" not in fname
          and "ip" not in fname
          and "closure" not in fname
          and "history" not in fname
          and "group" not in fname
          and "payouts" not in fname
          and "earnings" not in fname
      ):
        if "user_name" in content.lower() or "user_id" in content.lower():
          return content
    for fname, content in extracted_files.items():
      if "user_name" in content.lower():
        return content
    return None

  # 1. استخراج user_name و created_utc
  username = "غير معروف"
  creation_date = "غير معروف"
  user_content = get_user_content()

  if user_content:
    rows = parse_csv_text(user_content)
    if len(rows) >= 2:
      header = [h.lower() for h in rows[0]]
      data = rows[1]

      name_idx = (
          header.index("user_name") if "user_name" in header else -1
      )
      date_idx = (
          header.index("created_utc") if "created_utc" in header else -1
      )

      if name_idx != -1 and name_idx < len(data) and data[name_idx]:
        username = data[name_idx]
      elif len(data) > 1 and data[1]:
        username = data[1]

      if date_idx != -1 and date_idx < len(data) and data[date_idx]:
        creation_date = data[date_idx]
      elif len(data) > 5 and data[5]:
        creation_date = data[5]

  # استخراج الدولة (is_current=True)
  detected_country = "غير متوفر"
  loc_content = get_file_content(["location"]) or get_file_content(["ip"])
  if loc_content:
    for line in loc_content.splitlines():
      if "true" in line.lower():
        parts = [p.strip() for p in line.split(",")]
        for p in parts:
          if (
              p.lower() not in ["true", "false", "unknown"]
              and not p.isdigit()
              and len(p) >= 3
          ):
            detected_country = p
            break

  # 2. تحليل وتتبع الإيميلات
  email_content = get_file_content(["email"])
  email_audit_logs = []
  verified_history = []
  first_true_false_found = False
  initial_owner_email = None
  initial_owner_date = None

  if email_content:
    rows = parse_csv_text(email_content)
    for line_num, parts in enumerate(rows, 1):
      if not parts or len(parts) < 3:
        continue
      row_str = ",".join(parts).lower()
      if "email_address" in row_str or "user_id" in row_str:
        continue

      email = next((p for p in parts if "@" in p and "." in p), "")
      status_flags = [
          p.lower() for p in parts if p.lower() in ["true", "false"]
      ]

      if len(status_flags) >= 2 and email:
        is_ver = status_flags[0] == "true"
        is_dis = status_flags[1] == "true"
        added_date = (
            next((p for p in parts if re.match(r"^\d{4}-\d{2}-\d{2}", p)), None)
            or "غير متوفر"
        )

        if not first_true_false_found:
          if is_ver and not is_dis:
            first_true_false_found = True
            initial_owner_email = email
            initial_owner_date = added_date
            email_audit_logs.append(
                f"• [السطر {line_num}] المالك الأول الأصلي: {email} (موثق في:"
                f" {added_date})"
            )
            verified_history.append({"email": email, "date": added_date})
        else:
          if is_ver and is_dis:
            email_audit_logs.append(
                f"• [السطر {line_num}] تحذير: فقدان ملكية للإيميل {email}"
                " (True,True)"
            )
          elif is_ver and not is_dis:
            email_audit_logs.append(
                f"• [السطر {line_num}] ربط إيميل جديد بنجاح: {email}"
                " (True,False)"
            )
            verified_history.append({"email": email, "date": added_date})

  unique_verified_emails = []
  for item in verified_history:
    if item["email"] not in unique_verified_emails:
      unique_verified_emails.append(item["email"])

  # 3. تحليل الفواتير وتنظيف المعاملات (إزالة g-, apple-, ct-)
  billing_content = get_file_content(["billing", "bill", "purchase"])
  transactions = []

  if billing_content:
    for line in billing_content.splitlines():
      line_str = line.strip()
      if not line_str or "provider_payment_id" in line_str.lower():
        continue

      tx_id = None
      if "apple-" in line_str:
        m = re.search(r"apple-(\d+)", line_str)
        if m:
          tx_id = m.group(1)
      elif "ct-" in line_str:
        m = re.search(r"ct-([0-9a-fA-F-]+)", line_str)
        if m:
          tx_id = m.group(1)
      elif "g-GPA" in line_str or "GPA." in line_str:
        m = re.search(r"(GPA\.\d{4}-\d{4}-\d{4}-\d{5})", line_str)
        if m:
          tx_id = m.group(1)

      if tx_id:
        date_match = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", line_str)
        timestamp = (
            date_match.group(0) if date_match else "2026-00-00 00:00:00"
        )

        product = "Robux/Package"
        prod_m = re.search(r"(\d+\s*Robux|[A-Za-z\s]+Crown)", line_str)
        if prod_m:
          product = prod_m.group(1)

        transactions.append(
            {"id": tx_id, "timestamp": timestamp, "product": product}
        )

    transactions.sort(key=lambda x: x["timestamp"])

  first_receipt = (
      transactions[0]
      if transactions
      else {"id": "N/A", "product": "", "timestamp": ""}
  )
  latest_receipt = (
      transactions[-1]
      if transactions
      else {"id": "N/A", "product": "", "timestamp": ""}
  )

  # بناء تقرير الـ Markdown
  md_content = f"""# تقرير الأدلة والتحليل المعتمد لدعم روبلوكس (Roblox Support Evidence Report)

## 1. بيانات الملف الشخصي للحساب (Account Profile Info)
- **اسم الحساب (Username):** {username}
- **تاريخ الإنشاء (Creation Date):** {creation_date}
- **الدولة المسجلة الحقيقية (Registered Country):** {detected_country}

---

## 2. التحقق من ملكية وتاريخ البريد الإلكتروني (Email Ownership Verification)
- **المالك الأول الموثق للإنشاء:** `{initial_owner_email or 'غير متوفر'}` (تاريخ الربط: `{initial_owner_date or 'N/A'}`)

### 🔍 سجل تتبع وفحص الإيميلات المتتابعة:
"""
  if email_audit_logs:
    for log in email_audit_logs:
      md_content += f"{log}\n"
  else:
    md_content += "• لا توجد سجلات مطابقة للشروط.\n"

  md_content += f"""
### 🥇 أقوى الإيميلات الموثقة المقترحة للتواصل الفوري مع الدعم:
1. `{unique_verified_emails[0] if len(unique_verified_emails) > 0 else (initial_owner_email or 'غير متوفر')}`
2. `{unique_verified_emails[1] if len(unique_verified_emails) > 1 else 'غير متوفر'}`
3. `{unique_verified_emails[2] if len(unique_verified_emails) > 2 else 'غير متوفر'}`

---

## 3. إيصالات الشراء والفواتير المطلوبة (Billing Receipts)
- 🌟 **أول وصل شراء تاريخي (First Purchase Receipt):** `[{first_receipt['id']}]` | المنتج: {first_receipt['product']} | التاريخ: {first_receipt['timestamp']}
- 📌 **آخر وصل شراء (Latest Purchase Receipt):** `[{latest_receipt['id']}]` | المنتج: {latest_receipt['product']} | التاريخ: {latest_receipt['timestamp']}

---

## 4. الصيغة النهائية الجاهزة لتذكرة الدعم (Escalation Template)
Subject: Primary Ownership Verification & Recovery for Username: {username}

Body:
Hello Roblox Support Team,

I am requesting account recovery and primary ownership binding for my account **{username}** (Created on {creation_date}). 

Below are the verified historical email ownership records and purchase receipts confirming my absolute ownership:

- **First Verified Owner Email:** {initial_owner_email or 'N/A'} (Bound on: {initial_owner_date or 'N/A'})
- **First Historical Purchase Receipt:** {first_receipt['id']} ({first_receipt['product']}) - Date: {first_receipt['timestamp']}
- **Latest Purchase Receipt:** {latest_receipt['id']} ({latest_receipt['product']}) - Date: {latest_receipt['timestamp']}

Please secure my account and bind it exclusively to my active verified email address.

Thank you,
Username: {username}
"""
  return username, md_content


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
  doc = update.message.document
  if not doc.file_name.endswith(".zip"):
    await update.message.reply_text("❌ يرجى إرسال ملف بصيغة .zip فقط!")
    return

  await update.message.reply_text(
      "🔄 جاري تحميل وتحليل داتا روبلوكس بدقة..."
  )

  file = await context.bot.get_file(doc.file_id)
  zip_bytes = await file.download_as_bytearray()

  username, report = analyze_roblox_zip(zip_bytes)

  # إرسال التقرير النصي
  if len(report) > 4000:
    for i in range(0, len(report), 4000):
      await update.message.reply_text(report[i : i + 4000])
  else:
    await update.message.reply_text(report)

  # إرسال ملف .md جاهز باسم الحساب
  file_bytes = io.BytesIO(report.encode("utf-8"))
  safe_username = "".join(
      c for c in username if c.isalnum() or c in ("_", "-")
  )
  file_bytes.name = f"{safe_username}.md"
  await update.message.reply_document(
      document=file_bytes,
      caption=f"📁 ملف التقرير الجاهز للدعم الخاص بالحساب: {username}",
  )


if __name__ == "__main__":
  app = ApplicationBuilder().token(TOKEN).build()
  app.add_handler(MessageHandler(filters.Document.ZIP, handle_document))
  print("🤖 البوت يعمل الآن بنجاح...")
  app.run_polling()