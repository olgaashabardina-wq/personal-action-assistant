import json
import time
import csv
import io


from sqlalchemy.orm import Session
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.database import Base, engine, get_db
from fastapi import Depends, FastAPI, Form, HTTPException
from pydantic import ValidationError

from app.ai_service import extract_actions_from_text
from app.models import AIResult, ActionItem, AuditRun, InputText
from app.schemas import AIAnalysisResult, AuditResponse, TextCreate, TextResponse



app = FastAPI(
    title="Personal Action Assistant",
    description="API для превращения рабочего текста в структурированные действия",
    version="0.1.0",
)

@app.get("/")
def root():
    return RedirectResponse(url="/ui/new", status_code=303)

templates = Jinja2Templates(directory="templates")

Base.metadata.create_all(bind=engine)


def create_audit_run(
    db: Session,
    action: str,
    input_data: dict,
    output_data: dict | None,
    status: str,
    error: str | None,
    duration_ms: int,
) -> None:
    audit = AuditRun(
        action=action,
        input=json.dumps(input_data, ensure_ascii=False),
        output=json.dumps(output_data, ensure_ascii=False) if output_data is not None else None,
        status=status,
        error=error,
        duration_ms=duration_ms,
    )
    db.add(audit)
    db.commit()


@app.get("/")
def root():
    return {
        "service": "Personal Action Assistant",
        "status": "ok",
    }


@app.post("/texts", response_model=TextResponse)
def create_text(payload: TextCreate, db: Session = Depends(get_db)):
    start_time = time.perf_counter()

    input_text = InputText(
        source_text=payload.source_text,
        status="created",
        needs_review=False,
        review_reason=None,
    )

    db.add(input_text)
    db.commit()
    db.refresh(input_text)

    output_data = {
        "id": input_text.id,
        "status": input_text.status,
        "needs_review": input_text.needs_review,
    }

    duration_ms = int((time.perf_counter() - start_time) * 1000)

    create_audit_run(
        db=db,
        action="create_text",
        input_data=payload.model_dump(),
        output_data=output_data,
        status="success",
        error=None,
        duration_ms=duration_ms,
    )

    return input_text


@app.get("/texts", response_model=list[TextResponse])
def list_texts(db: Session = Depends(get_db)):
    return db.query(InputText).order_by(InputText.created_at.desc()).all()

@app.get("/audit-runs", response_model=list[AuditResponse])
def list_audit_runs(db: Session = Depends(get_db)):
    return db.query(AuditRun).order_by(AuditRun.created_at.desc()).all() 

def apply_review_rules(result: AIAnalysisResult) -> AIAnalysisResult:
    review_reasons = []

    relative_deadline_words = [
        "завтра",
        "послезавтра",
        "до пятницы",
        "к пятнице",
        "до понедельника",
        "к понедельнику",
        "на следующей неделе",
        "до конца недели",
        "до конца месяца",
        "пятница",
        "понедельник",
        "вторник",
        "среда",
        "четверг",
        "суббота",
        "воскресенье",
    ]

    if result.confidence < 0.75:
        review_reasons.append("Уверенность результата ниже 0.75.")

    if result.unclear_items:
        review_reasons.append("Есть неясные элементы во входном тексте.")

    for action in result.actions:
        deadline_text = (action.deadline or "").lower()
        source_text = (action.source_fragment or "").lower()

        if action.assignee is None:
            review_reasons.append(
                f"У задачи «{action.title}» не указан ответственный."
            )

        if action.deadline is None:
            review_reasons.append(
                f"У задачи «{action.title}» не указан срок выполнения."
            )

        if action.status == "needs_clarification":
            review_reasons.append(
                f"Задача «{action.title}» требует уточнения."
            )

        if any(word in deadline_text or word in source_text for word in relative_deadline_words):
            review_reasons.append(
                f"У задачи «{action.title}» указан относительный срок, его нужно подтвердить вручную."
            )

    if review_reasons:
        result.needs_review = True
        result.review_reason = " ".join(dict.fromkeys(review_reasons))
    else:
        result.needs_review = False
        result.review_reason = None

    return result     

    
@app.post("/texts/{text_id}/analyze", response_model=AIAnalysisResult)
def analyze_text(text_id: int, db: Session = Depends(get_db)):
    start_time = time.perf_counter()

    input_text = db.query(InputText).filter(InputText.id == text_id).first()

    if input_text is None:
        raise HTTPException(status_code=404, detail="Text not found")

    input_data = {
        "text_id": text_id,
        "source_text": input_text.source_text,
    }

    try:
        raw_result = extract_actions_from_text(input_text.source_text)

        validated_result = AIAnalysisResult.model_validate(raw_result)
        validated_result = apply_review_rules(validated_result)
        db.query(ActionItem).filter(ActionItem.input_text_id == input_text.id).delete()
        db.query(AIResult).filter(AIResult.input_text_id == input_text.id).delete()

        input_text.status = "needs_review" if validated_result.needs_review else "processed"
        input_text.needs_review = validated_result.needs_review
        input_text.review_reason = validated_result.review_reason

        ai_result = AIResult(
            input_text_id=input_text.id,
            summary=validated_result.summary,
            raw_json=json.dumps(validated_result.model_dump(), ensure_ascii=False),
            overall_priority=validated_result.overall_priority,
            confidence=str(validated_result.confidence),
            needs_review=validated_result.needs_review,
            review_reason=validated_result.review_reason,
        )

        db.add(ai_result)

        for action in validated_result.actions:
            action_item = ActionItem(
                input_text_id=input_text.id,
                title=action.title,
                description=action.description,
                assignee=action.assignee,
                department=action.department,
                deadline=action.deadline,
                priority=action.priority,
                status=action.status,
                source_fragment=action.source_fragment,
            )
            db.add(action_item)

        db.commit()

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        create_audit_run(
            db=db,
            action="analyze_text",
            input_data=input_data,
            output_data=validated_result.model_dump(),
            status="success" if not validated_result.needs_review else "needs_review",
            error=None,
            duration_ms=duration_ms,
        )

        return validated_result

    except ValidationError as exc:
        duration_ms = int((time.perf_counter() - start_time) * 1000)

        input_text.status = "needs_review"
        input_text.needs_review = True
        input_text.review_reason = "ИИ вернул JSON, который не прошёл проверку структуры."
        db.commit()

        create_audit_run(
            db=db,
            action="analyze_text",
            input_data=input_data,
            output_data=None,
            status="validation_error",
            error=str(exc),
            duration_ms=duration_ms,
        )

        raise HTTPException(
            status_code=422,
            detail="AI response did not match expected schema",
        )

    except Exception as exc:
        duration_ms = int((time.perf_counter() - start_time) * 1000)

        input_text.status = "error"
        db.commit()

        create_audit_run(
            db=db,
            action="analyze_text",
            input_data=input_data,
            output_data=None,
            status="error",
            error=str(exc),
            duration_ms=duration_ms,
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )

@app.get("/ui/new", response_class=HTMLResponse)
def ui_new_text(request: Request):
    return templates.TemplateResponse(
        "new_text.html",
        {
            "request": request,
            "title": "Новая запись",
        },
    )

@app.post("/ui/texts")
def ui_create_text_from_form(
    source_text: str = Form(...),
    db: Session = Depends(get_db),
):
    start_time = time.perf_counter()

    input_text = InputText(
        source_text=source_text,
        status="created",
        needs_review=False,
        review_reason=None,
    )

    db.add(input_text)
    db.commit()
    db.refresh(input_text)

    output_data = {
        "id": input_text.id,
        "status": input_text.status,
        "needs_review": input_text.needs_review,
    }

    duration_ms = int((time.perf_counter() - start_time) * 1000)

    create_audit_run(
        db=db,
        action="create_text_from_ui",
        input_data={"source_text": source_text},
        output_data=output_data,
        status="success",
        error=None,
        duration_ms=duration_ms,
    )

    return RedirectResponse(
        url=f"/ui/texts/{input_text.id}",
        status_code=303,
    )         

@app.get("/ui/texts", response_class=HTMLResponse)
def ui_texts_list(request: Request, db: Session = Depends(get_db)):
    texts = db.query(InputText).order_by(InputText.created_at.desc()).all()
    return templates.TemplateResponse(
        "texts_list.html",
        {
            "request": request,
            "title": "Витрина записей",
            "texts": texts,
        },
    )

@app.post("/ui/texts/{text_id}/analyze")
def ui_analyze_text_from_card(
    text_id: int,
    db: Session = Depends(get_db),
):
    analyze_text(text_id=text_id, db=db)

    return RedirectResponse(
        url=f"/ui/texts/{text_id}",
        status_code=303,
    )


@app.get("/ui/texts/{text_id}", response_class=HTMLResponse)
def ui_text_detail(text_id: int, request: Request, db: Session = Depends(get_db)):
    text = db.query(InputText).filter(InputText.id == text_id).first()

    if text is None:
        raise HTTPException(status_code=404, detail="Text not found")

    ai_result = (
        db.query(AIResult)
        .filter(AIResult.input_text_id == text_id)
        .order_by(AIResult.created_at.desc())
        .first()
    )

    actions = db.query(ActionItem).filter(ActionItem.input_text_id == text_id).all()

    audits = (
        db.query(AuditRun)
        .filter(AuditRun.input.contains(f'"text_id": {text_id}'))
        .order_by(AuditRun.created_at.desc())
        .all()
    )

    return templates.TemplateResponse(
        "text_detail.html",
        {
            "request": request,
            "title": f"Карточка записи #{text_id}",
            "text": text,
            "ai_result": ai_result,
            "actions": actions,
            "audits": audits,
        },
    )


@app.get("/ui/review", response_class=HTMLResponse)
def ui_review_list(request: Request, db: Session = Depends(get_db)):
    texts = (
        db.query(InputText)
        .filter(InputText.needs_review.is_(True))
        .order_by(InputText.created_at.desc())
        .all()
    )

    return templates.TemplateResponse(
        "review_list.html",
        {
            "request": request,
            "title": "Требует проверки",
            "texts": texts,
        },
    )


@app.get("/export/texts.json")
def export_texts_json(db: Session = Depends(get_db)):
    texts = db.query(InputText).order_by(InputText.created_at.desc()).all()

    return [
        {
            "id": item.id,
            "source_text": item.source_text,
            "status": item.status,
            "needs_review": item.needs_review,
            "review_reason": item.review_reason,
            "created_at": item.created_at.isoformat(),
        }
        for item in texts
    ]


@app.get("/export/texts.csv")
def export_texts_csv(db: Session = Depends(get_db)):
    texts = db.query(InputText).order_by(InputText.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [            
            "ID",
            "Исходный текст",
            "Статус",
            "Требует проверки",
            "Причина проверки",      
            "Создано",            
        ]
    )

    status_map = {
        "created": "Создано",
        "processed": "Обработано",
        "needs_review": "Требует проверки",
        "error": "Ошибка",
    }

    for item in texts:
        writer.writerow(
            [
                item.id,
                item.source_text,
                status_map.get(item.status, item.status),
                "Да" if item.needs_review else "Нет",
                item.review_reason or "",
                item.created_at.isoformat(),
            ]
        )
    

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=texts_export.csv"},
    )


@app.post("/ui/actions/{action_id}/update")
def ui_update_action(
    action_id: int,
    title: str = Form(...),
    department: str = Form(""),
    assignee: str = Form(""),
    deadline: str = Form(""),
    priority: str = Form("medium"),
    status: str = Form("new"),
    db: Session = Depends(get_db),
):
    start_time = time.perf_counter()

    action = db.query(ActionItem).filter(ActionItem.id == action_id).first()

    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")

    text_id = action.input_text_id

    action.title = title.strip()
    action.department = department.strip() or None
    action.assignee = assignee.strip() or None
    action.deadline = deadline.strip() or None
    action.priority = priority

    if action.assignee and action.deadline and status != "done":
        action.status = "in_progress"
    else:
        action.status = status    

    actions = db.query(ActionItem).filter(ActionItem.input_text_id == text_id).all()
    input_text = db.query(InputText).filter(InputText.id == text_id).first()
    ai_result = (
        db.query(AIResult)
        .filter(AIResult.input_text_id == text_id)
        .order_by(AIResult.created_at.desc())
        .first()
    )

    missing_reasons = []

    for item in actions:
        if not item.assignee:
            missing_reasons.append(f"У задачи «{item.title}» не указан ответственный.")
        if not item.deadline:
            missing_reasons.append(f"У задачи «{item.title}» не указан срок выполнения.")

    if input_text:
        if missing_reasons:
            input_text.status = "needs_review"
            input_text.needs_review = True
            input_text.review_reason = " ".join(dict.fromkeys(missing_reasons))
        else:
            input_text.status = "processed"
            input_text.needs_review = False
            input_text.review_reason = None

    if ai_result:
        if missing_reasons:
            ai_result.needs_review = True
            ai_result.review_reason = " ".join(dict.fromkeys(missing_reasons))
        else:
            ai_result.needs_review = False
            ai_result.review_reason = None

    duration_ms = int((time.perf_counter() - start_time) * 1000)

    create_audit_run(
        db=db,
        action="update_action_from_ui",
        input_data={
            "action_id": action_id,
            "text_id": text_id,
            "title": title,
            "assignee": assignee,
            "deadline": deadline,
            "priority": priority,
            "status": status,
        },
        output_data={
            "text_id": text_id,
            "needs_review": input_text.needs_review if input_text else None,
            "review_reason": input_text.review_reason if input_text else None,
        },
        status="needs_review" if input_text and input_text.needs_review else "success",
        error=None,
        duration_ms=duration_ms,
    )

    db.commit()

    return RedirectResponse(
        url=f"/ui/texts/{text_id}",
        status_code=303,
    )

def is_relative_deadline(deadline: str | None) -> bool:
    if not deadline:
        return True

    value = deadline.lower().strip()

    relative_words = [
        "завтра",
        "послезавтра",
        "пятница",
        "понедельник",
        "вторник",
        "среда",
        "четверг",
        "суббота",
        "воскресенье",
        "до пятницы",
        "к пятнице",
        "до понедельника",
        "к понедельнику",
        "на следующей неделе",
        "до конца недели",
        "до конца месяца",
    ]

    return any(word in value for word in relative_words)  

@app.get("/ui/tasks", response_class=HTMLResponse)
def ui_tasks_list(request: Request, db: Session = Depends(get_db)):
    all_actions = (
        db.query(ActionItem)
        .filter(ActionItem.assignee.isnot(None))
        .filter(ActionItem.deadline.isnot(None))
        .filter(ActionItem.status != "needs_clarification")
        .order_by(ActionItem.id.desc())
        .all()
    )

    actions = [
        action
        for action in all_actions
        if not is_relative_deadline(action.deadline)
    ]

    return templates.TemplateResponse(
        "tasks_list.html",
        {
            "request": request,
            "title": "Задачи на контроле",
            "actions": actions,
        },
    )   
  
@app.post("/ui/actions/{action_id}/done")
def ui_mark_action_done(
    action_id: int,
    db: Session = Depends(get_db),
):
    start_time = time.perf_counter()

    action = db.query(ActionItem).filter(ActionItem.id == action_id).first()

    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")

    text_id = action.input_text_id

    action.status = "done"

    duration_ms = int((time.perf_counter() - start_time) * 1000)

    create_audit_run(
        db=db,
        action="mark_action_done",
        input_data={"action_id": action_id, "text_id": text_id},
        output_data={"status": "done"},
        status="success",
        error=None,
        duration_ms=duration_ms,
    )

    db.commit()

    return RedirectResponse(url="/ui/tasks", status_code=303)
    

@app.get("/ui/tasks", response_class=HTMLResponse)             
def ui_tasks_list(request: Request, db: Session = Depends(get_db)):
    all_actions = (
        db.query(ActionItem)
        .filter(ActionItem.assignee.isnot(None))
        .filter(ActionItem.deadline.isnot(None))
        .filter(ActionItem.status != "needs_clarification")
        .order_by(ActionItem.id.desc())
        .all()
    )

    actions = [
        action
        for action in all_actions
        if not is_relative_deadline(action.deadline)
    ]

    return templates.TemplateResponse(
        "tasks_list.html",
        {
            "request": request,
            "title": "Задачи на контроле",
            "actions": actions,
        },
    )

    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")

    action.status = "done"

    duration_ms = int((time.perf_counter() - start_time) * 1000)

    create_audit_run(
        db=db,
        action="mark_action_done",
        input_data={
            "action_id": action.id,
            "text_id": action.input_text_id,
            "title": action.title,
        },
        output_data={
            "status": "done",
        },
        status="success",
        error=None,
        duration_ms=duration_ms,
    )

    db.commit()

    return RedirectResponse(
        url="/ui/tasks",
        status_code=303,
    )    