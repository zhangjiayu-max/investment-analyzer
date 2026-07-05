"""AI 建议准确率分析路由 — /api/suggestion-accuracy/*"""

from fastapi import APIRouter, Query
from services.suggestion_accuracy import analyze_suggestion_accuracy, format_report

router = APIRouter(prefix="/api/suggestion-accuracy", tags=["suggestion-accuracy"])


@router.get("")
@router.get("/")
def get_suggestion_accuracy(
    days_back: int = Query(30, ge=1, le=365, description="回溯天数"),
):
    """获取 AI 建议准确率分析结果。"""
    return analyze_suggestion_accuracy(days_back=days_back)


@router.get("/report")
def get_suggestion_accuracy_report(
    days_back: int = Query(30, ge=1, le=365, description="回溯天数"),
):
    """获取可读文本格式的准确率报告。"""
    result = analyze_suggestion_accuracy(days_back=days_back)
    return {"report": format_report(result), "summary": {
        "total": result["total_suggestions"],
        "accuracy": result["accuracy"],
        "adopted": result["adopted"],
    }}
