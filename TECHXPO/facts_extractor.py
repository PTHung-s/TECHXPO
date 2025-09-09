# facts_extractor.py
# -*- coding: utf-8 -*-
"""
Module trích xuất facts có thể tái sử dụng và tạo summary từ lịch sử hội thoại
Sử dụng Gemini 2.5 Flash để phân tích và tổng hợp thông tin
"""

import os
import json
from typing import Dict, Any, Optional
from google import genai
from google.genai import types
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv(".env.local") or load_dotenv()

class FactsAndSummary(BaseModel):
    """Schema cho output của facts extractor"""
    facts: str  # Các facts có thể tái sử dụng (thông tin bệnh nhân, tiền sử, allergies, etc.)
    summary: str  # Tóm tắt cuộc hội thoại để làm context cho lần sau

def _get_gemini_client():
    """Tạo client Gemini"""
    api_key = os.getenv("GOOGLE_API_KEY2")
    if not api_key:
        raise ValueError("GEMINI_API_KEY2 not found in environment")
    return genai.Client(api_key=api_key)

EXTRACTION_PROMPT = """
Bạn là chuyên gia phân tích hồ sơ y tế. Nhiệm vụ của bạn là trích xuất và tổng hợp thông tin từ cuộc hội thoại khám bệnh.

HƯỚNG DẪN:
1. FACTS: Trích xuất các thông tin FACTS có thể tái sử dụng cho các lần khám sau:
   - Thông tin cá nhân cơ bản (tuổi, nghề nghiệp, điều kiện sống)
   - Tiền sử bệnh (bệnh mạn tính, phẫu thuật, tai nạn)
   - Dị ứng thuốc/thực phẩm
   - Thói quen sinh hoạt quan trọng (hút thuốc, uống rượu, tập thể dục)
   - Thuốc đang dùng thường xuyên
   - Bệnh di truyền gia đình
   - Các triệu chứng mạn tính/tái phát
   - Sở thích, phong cách, thái độ nói chuyện.

2. SUMMARY: Tạo tóm tắt ngắn gọn về cuộc hội thoại này:
   - Lý do khám chính
   - Triệu chứng hiện tại và thời gian xuất hiện
   - Kế hoạch điều trị/xét nghiệm
   - Lưu ý đặc biệt
   - Chú ý: những gạch đầu dòng trên phải được viết có dấu ':' và theo sau đó là nội dung nhé

QUY TẮC:
- Chỉ ghi thông tin được đề cập rõ ràng, không suy đoán
- Facts phải là thông tin ổn định, không thay đổi theo thời gian
- Summary tập trung vào lần khám hiện tại
- Ngôn ngữ ngắn gọn, chuyên nghiệp
- Nếu có facts cũ, hãy tích hợp và cập nhật (không trùng lặp)

INPUT:
Cuộc hội thoại mới: {new_conversation}

Facts cũ (nếu có): {existing_facts}

Summary cũ (nếu có): {existing_summary}

Trích xuất và trả về JSON với format:
{{
  "facts": "Thông tin facts tích hợp",
  "summary": "Summary của lần khám này"
}}
"""

def extract_facts_and_summary(
    new_conversation: str,
    existing_facts: str = "",
    existing_summary: str = "",
    model_name: str = "gemini-2.5-flash"
) -> Dict[str, str]:
    """
    Trích xuất facts và tạo summary từ cuộc hội thoại mới
    
    Args:
        new_conversation: Lịch sử hội thoại mới hoàn chỉnh
        existing_facts: Facts đã có từ các lần khám trước
        existing_summary: Summary của lần khám trước (để tham khảo context)
        model_name: Tên model Gemini để sử dụng
        
    Returns:
        Dict với keys 'facts' và 'summary'
    """
    if not new_conversation.strip():
        return {"facts": existing_facts, "summary": existing_summary}
    
    try:
        client = _get_gemini_client()
        
        # Prepare prompt
        prompt = EXTRACTION_PROMPT.format(
            new_conversation=new_conversation.strip(),
            existing_facts=existing_facts.strip() if existing_facts else "(Chưa có)",
            existing_summary=existing_summary.strip() if existing_summary else "(Chưa có)"
        )
        
        # Generate with structured output
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT"],
                response_schema=FactsAndSummary,
                temperature=0.1,  # Low temperature for consistent extraction
            ),
        )
        
        # Parse response
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'content') and candidate.content.parts:
                content_text = candidate.content.parts[0].text
                try:
                    result = json.loads(content_text)
                    facts_out = result.get("facts", existing_facts)
                    summary_out = result.get("summary", "")
                    print(f"[FactsExtractor] parsed JSON facts_len={len(facts_out)} summary_len={len(summary_out)}")
                    return {"facts": facts_out, "summary": summary_out}
                except json.JSONDecodeError:
                    facts = _extract_section(content_text, "facts") or existing_facts
                    summary = _extract_section(content_text, "summary") or ""
                    print(f"[FactsExtractor] fallback parse facts_len={len(facts)} summary_len={len(summary)}")
                    return {"facts": facts, "summary": summary}
        
        # Fallback if no valid response
        return {"facts": existing_facts, "summary": "Không thể tạo summary từ cuộc hội thoại này."}
        
    except Exception as e:
        print(f"Error in extract_facts_and_summary: {e}")
        return {
            "facts": existing_facts,
            "summary": f"Lỗi xử lý: {str(e)}"
        }

def _extract_section(text: str, section: str) -> Optional[str]:
    """Extract a specific section from text response"""
    try:
        # Look for JSON-like structure
        if "{" in text and "}" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            json_part = text[start:end]
            data = json.loads(json_part)
            return data.get(section, "")
        
        # Fallback: look for section headers
        lines = text.split('\n')
        in_section = False
        content = []
        
        for line in lines:
            if section.lower() in line.lower() and (":" in line or line.strip().startswith(section)):
                in_section = True
                # Extract content after colon if present
                if ":" in line:
                    after_colon = line.split(":", 1)[1].strip()
                    if after_colon:
                        content.append(after_colon)
                continue
            elif in_section and (line.strip().startswith("summary") or line.strip().startswith("facts")):
                # Reached next section
                break
            elif in_section and line.strip():
                content.append(line.strip())
        
        return '\n'.join(content).strip() if content else None
        
    except Exception:
        return None

def merge_facts(old_facts: str, new_facts: str) -> str:
    """
    Utility function để merge facts cũ và mới (simple concatenation với dedup)
    """
    if not old_facts.strip():
        return new_facts
    if not new_facts.strip():
        return old_facts
    
    # Simple merge - could be enhanced with more sophisticated deduplication
    combined = f"{old_facts.strip()}\n\n--- Cập nhật mới ---\n{new_facts.strip()}"
    return combined

# Convenience function cho backward compatibility
def update_patient_context(
    conversation_history: str,
    current_facts: str = "",
    previous_summary: str = ""
) -> Dict[str, str]:
    """
    Wrapper function với tên dễ hiểu hơn
    """
    return extract_facts_and_summary(
        new_conversation=conversation_history,
        existing_facts=current_facts,
        existing_summary=previous_summary
    )

if __name__ == "__main__":
    # Simple test
    test_conversation = """
    [user] Chào bác sĩ, tôi bị đau đầu từ 3 ngày nay
    [assistant] Xin chào, cho bác sĩ hỏi anh bao nhiêu tuổi và nghề nghiệp gì?
    [user] Tôi 35 tuổi, làm lập trình viên, hay ngồi máy tính
    [assistant] Đau đầu có kèm theo triệu chứng gì khác không?
    [user] Có hơi chóng mặt và mắt mờ. Tôi có tiền sử cao huyết áp
    [assistant] Anh có đang uống thuốc điều trị cao huyết áp không?
    [user] Có, đang uống Amlodipine 5mg mỗi ngày
    """
    
    result = extract_facts_and_summary(test_conversation)
    print("=== KẾT QUẢ TEST ===")
    print(f"Facts: {result['facts']}")
    print(f"Summary: {result['summary']}")
