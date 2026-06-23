"""对比分析AI差异 — 比较两次分析结果的差异。"""
import asyncio
import logging

from fastapi import APIRouter

from db.config import get_config_int, get_config_float
from llm_service import _call_llm

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analysis-compare"])


@router.post("/api/portfolio/analysis/compare-diff")
async def compare_diff_api(data: dict):
    """AI 分析两次诊断结果的差异。"""
    record_a = data.get("record_a", {})
    record_b = data.get("record_b", {})
    analysis_type = data.get("type", "panorama")

    result_a = record_a.get("result", "")
    result_b = record_b.get("result", "")

    if not result_a or not result_b:
        return {"analysis": "两次诊断结果不完整，无法对比。"}

    prompt = f"""请分析以下两次{analysis_type}诊断结果的差异，给出简洁的结论：

## 第一次诊断（{record_a.get('created_at', '未知')}）
{str(result_a)[:2000]}

## 第二次诊断（{record_b.get('created_at', '未知')}）
{str(result_b)[:2000]}

请从以下角度分析：
1. 核心变化：哪些指标改善了？哪些恶化了？
2. 原因推测：可能的原因是什么？
3. 需要关注：有哪些变化需要特别关注？
4. 建议行动：基于变化，建议采取什么行动？

请用简洁的中文回答，分点列出。"""

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(lambda: _call_llm(
                caller="compare_diff",
                model="mimo-v2.5-pro",
                messages=[{"role": "user", "content": prompt}],
                temperature=get_config_float('llm.temperature_default', 0.3),
                max_tokens=get_config_int('llm.max_tokens_report', 8192),
            )),
            timeout=120
        )
        content = response.choices[0].message.content or ""
        return {"analysis": content}
    except asyncio.TimeoutError:
        return {"analysis": "分析超时，请重试。"}
    except Exception as e:
        logger.warning(f"对比分析失败: {e}")
        return {"analysis": f"分析失败: {str(e)}"}
