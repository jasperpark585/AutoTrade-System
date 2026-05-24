from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import os
import sys
from typing import Any

import streamlit as st

CODE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.getenv("YTA_RUNTIME_ROOT", str(CODE_ROOT))).resolve()
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from app.config import load_config
from app.generation.content_generator import ContentGenerator
from app.models import RankedTopic, TopicDetail
from app.pipeline import Pipeline
from app.runtime.control import clear_studio_session, read_studio_access, read_studio_session
from app.runtime.server_sync import (
    arm_next_run_monitor,
    fetch_server_runtime_status,
    load_remote_target,
    read_server_sync_state,
    reconcile_server_settings,
    sync_server_settings,
)
from app.runtime.windows_scheduler import query_windows_scheduled_task, uninstall_windows_scheduled_task
from app.runtime.windows_scheduler import install_windows_scheduled_task
from app.storage.repository import StorageRepository
from app.studio.channel_paths import resolve_youtube_token_file, stored_youtube_token_file
from app.studio.models import (
    AutomationSettings,
    ChannelProfile,
    DEFAULT_CAPTION_CERTIFICATION,
    DEFAULT_TUNNEL_NAME,
    RemoteAccessSettings,
)
from app.studio.presets import PRESET_DEFINITIONS, preset_by_key
from app.studio.store import DEFAULT_SHORTS_UPLOAD_TIMES, StudioSettingsStore
from app.utils.logging import configure_logging
from app.youtube.policy import decide_contains_synthetic_media
from app.youtube.uploader import FULL_SCOPE, MANAGE_SCOPE, READONLY_SCOPE, UPLOAD_SCOPE, YouTubeUploader


CATEGORY_OPTIONS = {
    "25": "25 - News & Politics",
    "24": "24 - Entertainment",
    "27": "27 - Education",
    "22": "22 - People & Blogs",
    "28": "28 - Science & Technology",
}

REMOTE_MODE_OPTIONS = {
    "quick": "Quick Tunnel",
    "named": "Named Tunnel",
}

VISUAL_STYLE_OPTIONS = [
    "premium_news_graphic",
    "editorial_ai_art",
    "calm_editorial",
    "photoreal_ai_people",
    "photoreal_reenactment",
    "synthetic_avatar",
]

HOOK_MOTION_OPTIONS = {
    "dramatic_push": "Dramatic Push",
    "reveal_pan": "Reveal Pan",
    "slow_drift": "Slow Drift",
}

st.set_page_config(
    page_title="YouTube Control Studio",
    page_icon="YT",
    layout="wide",
)


def main() -> None:
    _startup_reconcile_server_settings()
    _apply_css()
    _render_hero()
    _render_access_panel()

    store = StudioSettingsStore(PROJECT_ROOT / "data" / "studio_settings.json")
    settings = store.load()
    selected_channel_id = _channel_selector(settings.channels, settings.active_channel_id)
    profile = _find_channel(settings.channels, selected_channel_id)
    _seed_form_state(profile, settings.automation, settings.remote_access)

    _render_sidebar(store, settings, profile)
    _render_channel_portfolio(settings.channels, selected_channel_id)

    header_cols = st.columns([3, 1])
    header_cols[0].markdown(
        f"""
        <div class="section-title">Channel Studio</div>
        <div class="section-subtitle">{profile.display_name} 채널의 업로드 규칙, 일정, 수동 실행, 서버 연동을 한 번에 관리합니다.</div>
        """,
        unsafe_allow_html=True,
    )
    if header_cols[1].button("Save & Sync", type="primary", use_container_width=True):
        _save_all_settings(store, settings, profile)
        st.rerun()

    tabs = st.tabs(["채널 설정", "업로드/자동화", "수동 실행", "최근 실행 기록"])
    with tabs[0]:
        _render_channel_settings_tab()
    with tabs[1]:
        _render_upload_settings_tab(profile)
        _render_schedule_panel(store, settings, profile)
        _render_automation_tab()
        _render_server_runtime_panel()
    with tabs[2]:
        _render_manual_tab(store, settings, profile)
    with tabs[3]:
        _render_history_tab(profile)


def _startup_reconcile_server_settings() -> dict[str, Any]:
    if "_startup_reconcile_result" in st.session_state:
        return st.session_state._startup_reconcile_result
    result = reconcile_server_settings(PROJECT_ROOT)
    if str(result.get("status") or "").strip() == "success":
        uninstall_windows_scheduled_task()
    st.session_state._startup_reconcile_result = result
    return result


def _apply_css() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;700;800&family=Playfair+Display:wght@700&display=swap');
        .stApp {
            font-family: 'Manrope', sans-serif;
            background:
                radial-gradient(circle at top left, rgba(15, 118, 110, 0.10), transparent 24%),
                radial-gradient(circle at top right, rgba(180, 83, 9, 0.12), transparent 26%),
                linear-gradient(180deg, #f8f4ec 0%, #f4eee2 44%, #ece6db 100%);
            color: #172033;
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, rgba(255,255,255,0.94), rgba(249,245,237,0.96));
            border-right: 1px solid rgba(148, 163, 184, 0.18);
        }
        .hero-shell {
            padding: 1.6rem 1.8rem;
            border-radius: 28px;
            background: linear-gradient(135deg, rgba(255,255,255,0.92), rgba(255,248,239,0.88));
            border: 1px solid rgba(148, 163, 184, 0.16);
            box-shadow: 0 24px 50px rgba(120, 113, 108, 0.12);
            margin-bottom: 1rem;
        }
        .hero-kicker {
            font-size: 0.82rem;
            letter-spacing: 0.18em;
            text-transform: uppercase;
            color: #0f766e;
            font-weight: 800;
        }
        .hero-title {
            font-family: 'Playfair Display', serif;
            font-size: 3rem;
            color: #101827;
            margin-top: 0.3rem;
        }
        .hero-copy {
            margin-top: 0.7rem;
            max-width: 760px;
            color: #475569;
            font-size: 1rem;
            line-height: 1.7;
        }
        .section-title {
            font-family: 'Playfair Display', serif;
            font-size: 2.05rem;
            color: #0f172a;
        }
        .section-subtitle {
            color: #64748b;
            margin-top: 0.25rem;
        }
        .channel-card {
            padding: 1rem 1.05rem;
            border-radius: 22px;
            background: rgba(255,255,255,0.86);
            border: 1px solid rgba(148,163,184,0.16);
            box-shadow: 0 18px 38px rgba(120,113,108,0.08);
            min-height: 178px;
        }
        .channel-card.active {
            border-color: rgba(15,118,110,0.45);
            box-shadow: 0 18px 42px rgba(15,118,110,0.16);
        }
        .channel-name {
            font-size: 1.08rem;
            font-weight: 800;
            color: #0f172a;
        }
        .channel-meta {
            margin-top: 0.45rem;
            color: #64748b;
            font-size: 0.92rem;
            line-height: 1.6;
        }
        .metric-card {
            padding: 1rem 1.1rem;
            border-radius: 22px;
            background: rgba(255,255,255,0.82);
            border: 1px solid rgba(148,163,184,0.18);
            box-shadow: 0 18px 36px rgba(120,113,108,0.08);
        }
        .metric-label {
            font-size: 0.8rem;
            color: #0f766e;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-weight: 700;
        }
        .metric-value {
            margin-top: 0.3rem;
            font-size: 1.05rem;
            color: #111827;
            font-weight: 800;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_hero() -> None:
    st.markdown(
        """
        <div class="hero-shell">
            <div class="hero-kicker">Multi Channel Automation</div>
            <div class="hero-title">YouTube Control Studio</div>
            <div class="hero-copy">
                뉴스, 복지, 명언, 인생사연 롱폼 채널을 각각 따로 설정하고 자동 업로드 시간까지 개별 관리합니다.
                서버 연동 상태와 수동 실행 흐름도 한 화면에서 확인할 수 있습니다.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_access_panel() -> None:
    access = read_studio_access(PROJECT_ROOT)
    server_status = _load_server_runtime_status() if _server_status_available() else {}
    startup_sync = st.session_state.get("_startup_reconcile_result", {})
    server_remote = ""
    if isinstance(server_status, dict) and server_status.get("status") in {"success", "cached"}:
        server_remote = str(server_status.get("studio_public_url") or "").strip()
    if not access and not server_remote:
        return
    cols = st.columns(4)
    values = [
        ("Local", access.get("local_url", "") or "not available"),
        ("LAN", access.get("lan_url", "") or "not available"),
        ("This Device Remote", access.get("public_url", "") or "not available"),
        ("Server Remote", server_remote or "not available"),
    ]
    for col, (label, value) in zip(cols, values):
        col.markdown(
            f"<div class='metric-card'><div class='metric-label'>{label}</div><div class='metric-value'>{value}</div></div>",
            unsafe_allow_html=True,
        )
    if startup_sync:
        sync_status = str(startup_sync.get("status") or "").strip()
        sync_message = str(startup_sync.get("message") or "").strip()
        if sync_message:
            if sync_status == "failed":
                st.warning(f"Server settings sync check: {sync_message}")
            elif sync_status == "pending":
                st.warning(f"Pending server sync: {sync_message}")
            elif sync_status == "success":
                st.caption(f"Settings sync: {sync_message}")


def _channel_selector(channels: list[ChannelProfile], active_channel_id: str) -> str:
    if "selected_channel_id" not in st.session_state:
        st.session_state.selected_channel_id = active_channel_id
    selected = st.sidebar.selectbox(
        "유튜브 채널 선택",
        options=[channel.id for channel in channels],
        format_func=lambda channel_id: _channel_label(channels, channel_id),
        index=_channel_index(channels, st.session_state.selected_channel_id),
    )
    st.session_state.selected_channel_id = selected
    return selected


def _render_sidebar(store: StudioSettingsStore, settings, profile: ChannelProfile) -> None:
    st.sidebar.markdown("### Quick Actions")
    st.sidebar.info("설정을 저장하면 서버용 설정 파일도 함께 반영됩니다. 자동 업로드는 서버에서 계속 동작합니다.")

    if st.sidebar.button("현재 채널을 기본 채널로 지정", use_container_width=True):
        settings.active_channel_id = profile.id
        store.save(settings)
        st.success("기본 채널이 변경되었습니다.")

    if st.sidebar.button("Studio 종료", use_container_width=True):
        clear_studio_session(PROJECT_ROOT)
        os._exit(0)


def _render_channel_portfolio(channels: list[ChannelProfile], selected_channel_id: str) -> None:
    cols = st.columns(len(channels))
    for col, channel in zip(cols, channels):
        preset = preset_by_key(channel.preset_key)
        active_class = "active" if channel.id == selected_channel_id else ""
        col.markdown(
            f"""
            <div class="channel-card {active_class}">
                <div class="channel-name">{channel.display_name}</div>
                <div class="channel-meta">
                    프리셋: {preset.label}<br>
                    업로드: {_channel_schedule_label(channel)}<br>
                    영상 길이: 약 {int(channel.content_duration_seconds // 60) if channel.content_duration_seconds >= 300 else channel.content_duration_seconds}{"분" if channel.content_duration_seconds >= 300 else "초"}<br>
                    채널 ID: {channel.youtube_channel_id or "-"}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_channel_settings_tab() -> None:
    preset = preset_by_key(st.session_state.preset_key)
    left, right = st.columns([1.15, 0.85])
    with left:
        st.text_input("표시 채널명", key="display_name")
        st.text_input("유튜브 채널명", key="youtube_channel_title")
        st.text_input("유튜브 채널 ID", key="youtube_channel_id")
        st.selectbox(
            "콘텐츠 프리셋",
            options=list(PRESET_DEFINITIONS.keys()),
            format_func=lambda key: PRESET_DEFINITIONS[key].label,
            key="preset_key",
        )
        st.selectbox("비주얼 스타일", options=VISUAL_STYLE_OPTIONS, key="visual_style")
        st.number_input("목표 영상 길이(초)", min_value=45, max_value=7200, step=15, key="content_duration_seconds")

        if _is_story_channel():
            story_cols = st.columns(3)
            story_cols[0].number_input("씬 수", min_value=3, max_value=12, step=1, key="story_scene_count")
            story_cols[1].number_input("훅 길이(초)", min_value=20, max_value=120, step=5, key="hook_duration_seconds")
            story_cols[2].number_input("씬당 이미지 수", min_value=1, max_value=6, step=1, key="story_images_per_scene")
            st.caption("롱폼 인생사연 채널은 훅 전용 모션, 씬당 다중 이미지 교차, 씬별 TTS, 자막 번인과 배경음악까지 함께 렌더링합니다.")

    with right:
        st.markdown("#### 프리셋 안내")
        st.info(preset.description)
        if st.button("선택 프리셋 기본값 다시 적용", use_container_width=True):
            _apply_preset_defaults_to_state(st.session_state.preset_key)
            st.rerun()

        st.markdown("#### 생성 규칙")
        st.text_input("제목 앞문구", key="title_prefix")
        st.text_input("제목 뒷문구", key="title_suffix")
        st.text_area("구독 유도 문구", key="call_to_action", height=90)
        st.text_area("추가 제작 지시", key="extra_instructions", height=140)

    keyword_cols = st.columns(2)
    keyword_cols[0].text_area("포함 키워드", key="topic_include_keywords_text", height=120)
    keyword_cols[1].text_area("제외 키워드", key="topic_exclude_keywords_text", height=120)

    option_cols = st.columns(3)
    option_cols[0].checkbox("AI 대본 사용", key="use_ai_text")
    option_cols[1].checkbox("AI 이미지 사용", key="use_ai_images")
    option_cols[2].checkbox("AI 메타데이터 사용", key="use_ai_metadata")


def _render_upload_settings_tab(profile: ChannelProfile) -> None:
    st.markdown("#### 업로드 / YouTube 설정")
    cols = st.columns(3)
    cols[0].selectbox("공개 범위", options=["private", "unlisted", "public"], key="privacy_status")
    cols[0].selectbox(
        "카테고리",
        options=list(CATEGORY_OPTIONS.keys()),
        format_func=lambda key: CATEGORY_OPTIONS.get(key, key),
        key="category_id",
    )
    cols[0].checkbox("아동용 아님", key="not_for_kids")

    cols[1].text_input("기본 언어", key="default_language")
    cols[1].text_input("오디오 언어", key="default_audio_language")
    cols[1].selectbox("변경된 콘텐츠 표시", options=["auto", "no", "yes"], key="altered_content_mode")

    cols[2].checkbox("수동 synthetic media 강제", key="manual_contains_synthetic")
    cols[2].text_input("캡션 인증 문구", key="caption_certification_hint")
    auto_decision = _auto_decision(profile)
    cols[2].metric("자동 synthetic 판단", "예" if auto_decision else "아니오")

    auth_cols = st.columns(2)
    auth_cols[0].text_input("YouTube OAuth Client Secrets 경로", key="youtube_client_secrets_file")
    auth_cols[1].text_input("YouTube OAuth Token 경로", key="youtube_token_file")
    auth_status = _youtube_auth_status(profile)
    video_profile = _channel_video_profile(profile)
    status_cols = st.columns(5)
    status_cols[0].metric("Client secrets", "준비됨" if auth_status["client_secrets_exists"] else "없음")
    status_cols[1].metric("채널 토큰", "준비됨" if auth_status["token_exists"] else "연결 필요")
    status_cols[2].metric("Upload scope", "yes" if auth_status["has_upload_scope"] else "no")
    status_cols[3].metric("Manage/Delete scope", "yes" if auth_status["has_manage_scope"] else "no")
    status_cols[4].metric("고정 출력 형식", video_profile["label"])
    st.caption(f"현재 채널 토큰 파일: {auth_status['token_path']}")
    st.caption(f"렌더 해상도: {video_profile['size']}")
    if auth_status["token_exists"]:
        st.caption(
            "Granted scopes: "
            + ", ".join(auth_status.get("scopes", []))
            if auth_status.get("scopes")
            else "Granted scopes: unavailable"
        )

    if _is_story_channel():
        st.markdown("#### 롱폼 렌더 옵션")
        render_cols = st.columns(2)
        render_cols[0].checkbox("자막 영상 번인", key="burn_in_subtitles")
        render_cols[0].selectbox(
            "훅 모션 템플릿",
            options=list(HOOK_MOTION_OPTIONS.keys()),
            format_func=lambda key: HOOK_MOTION_OPTIONS[key],
            key="hook_motion_template",
        )
        render_cols[1].text_input("배경음악 경로", key="background_music_path")
        render_cols[1].slider("배경음악 볼륨(%)", min_value=0, max_value=100, step=1, key="background_music_volume")
        st.caption(f"배경음악 경로를 비워두면 `{(PROJECT_ROOT / 'assets' / 'music').as_posix()}` 폴더의 첫 번째 오디오 파일을 자동 사용합니다.")

    st.markdown("#### 수동 덮어쓰기")
    manual_cols = st.columns(2)
    manual_cols[0].text_input("수동 제목", key="manual_title")
    manual_cols[1].text_input("수동 설명", key="manual_description")
    manual_cols = st.columns(2)
    manual_cols[0].text_input("수동 배경 이미지 경로", key="manual_background_path")
    manual_cols[1].text_input("수동 썸네일 이미지 경로", key="manual_thumbnail_path")
    manual_cols = st.columns(2)
    manual_cols[0].text_area("수동 배경 프롬프트", key="manual_background_prompt", height=100)
    manual_cols[1].text_area("수동 썸네일 프롬프트", key="manual_thumbnail_prompt", height=100)


def _render_schedule_panel(store: StudioSettingsStore, settings, profile: ChannelProfile) -> None:
    st.markdown("#### 업로드 일정")
    daily_times = _split_schedule_times(st.session_state.get("daily_upload_times_text", ""))
    persisted_profile = _persisted_profile(store, profile.id)
    persisted_label = _channel_schedule_label(persisted_profile)
    cols = st.columns([1, 1.8])
    cols[0].checkbox("자동 업로드 사용", key="schedule_enabled")
    cols[1].text_area(
        "업로드 시간 목록(HH:MM)",
        key="daily_upload_times_text",
        height=112,
        placeholder="06:00\n10:00\n14:00\n19:00",
    )
    if daily_times:
        st.info(
            "업로드 시간 목록에 값이 있으면 이 목록만 적용됩니다. 새 줄에 HH:MM 형식으로 시간을 추가하면 그 시간도 함께 적용됩니다."
        )
        st.caption(
            f"현재 적용 시간: {', '.join(daily_times)}. 반복 간격 설정은 목록을 비웠을 때만 사용됩니다."
        )
    else:
        interval_col = st.columns([1, 1.8])[0]
        interval_col.number_input("반복 간격(시간)", min_value=1, max_value=168, step=1, key="schedule_interval_hours")
        st.caption("업로드 시간 목록이 비어 있으면 반복 간격 기준으로 자동 업로드됩니다.")

    if _schedule_state_changed(profile):
        st.warning("이 일정 변경사항은 아직 저장되지 않았습니다. 아래 버튼을 눌러야 실제 업로드 스케줄에 반영됩니다.")
        save_cols = st.columns([1, 3])
        if save_cols[0].button("일정 저장/반영", use_container_width=True, key=f"save_schedule_{profile.id}"):
            _save_all_settings(store, settings, profile)
            st.rerun()
    else:
        st.success(f"현재 실제 적용 일정: {_selected_schedule_label()}")
    if persisted_profile.id == profile.id:
        st.caption(f"저장 파일 기준 일정: {persisted_label}")

def _render_automation_tab() -> None:
    st.markdown("#### 서버 자동화 / 원격 연결")
    cols = st.columns([1, 1])
    with cols[0]:
        st.number_input("전역 기본 간격(시간)", min_value=1, max_value=168, step=1, key="global_default_interval_hours")
        task_status = query_windows_scheduled_task()
        if task_status["status"] == "ready":
            st.warning(
                f"로컬 Windows 예약 작업이 남아 있습니다.\n\n상태: {task_status.get('Status', '-')}\n다음 실행: {task_status.get('Next Run Time', '-')}"
            )
        else:
            st.success("로컬 Windows 예약 작업은 비활성 상태입니다.")
        if st.button("로컬 예약 작업 제거", use_container_width=True):
            result = uninstall_windows_scheduled_task()
            if result["status"] in {"success", "missing", "skipped"}:
                st.success(result["message"])
            else:
                st.error(result["message"])

    with cols[1]:
        st.checkbox("원격 접속 사용", key="remote_access_enabled")
        st.selectbox(
            "원격 접속 방식",
            options=list(REMOTE_MODE_OPTIONS.keys()),
            format_func=lambda key: REMOTE_MODE_OPTIONS[key],
            key="remote_access_mode",
        )
        st.text_input("Tunnel 이름", key="remote_tunnel_name")
        st.text_input("고정 호스트명", key="remote_hostname")
        st.text_input("Tunnel Token", key="remote_tunnel_token", type="password")


def _render_server_runtime_panel() -> None:
    st.markdown("#### 서버 실행 상태")
    controls = st.columns([1, 1, 2])
    refresh_requested = controls[0].button("서버 상태 새로고침", use_container_width=True)
    monitor_requested = controls[1].button("다음 업로드 모니터", use_container_width=True)
    fetched_at = st.session_state.get("_server_runtime_status_fetched_at", "")
    if fetched_at:
        controls[2].caption(f"마지막 조회: {fetched_at}")

    if monitor_requested:
        monitor_result = arm_next_run_monitor(PROJECT_ROOT)
        if monitor_result.get("status") == "success":
            st.success(str(monitor_result.get("message", "")))
        else:
            st.warning(str(monitor_result.get("message", "다음 업로드 모니터를 시작하지 못했습니다.")))
        refresh_requested = True

    status = _load_server_runtime_status(force=refresh_requested)
    if status.get("status") not in {"success", "cached"}:
        st.warning(str(status.get("message", "서버 상태를 읽지 못했습니다.")))
        return
    if status.get("status") == "cached":
        st.warning(f"실시간 서버 상태를 읽지 못해 마지막 캐시 기준으로 표시 중입니다. {status.get('message', '')}".strip())

    selected_channel_id = str(st.session_state.get("selected_channel_id") or "").strip()
    selected_schedule = _selected_channel_runtime_schedule(status, selected_channel_id)
    latest_runs_by_channel = (
        status.get("latest_runs_by_channel") if isinstance(status.get("latest_runs_by_channel"), dict) else {}
    )
    selected_latest = latest_runs_by_channel.get(selected_channel_id, {}) if isinstance(latest_runs_by_channel, dict) else {}
    latest_upload = (
        selected_latest.get("upload")
        if isinstance(selected_latest.get("upload"), dict)
        else (status.get("latest_upload") if isinstance(status.get("latest_upload"), dict) else {})
    )
    next_due_at = str(selected_schedule.get("next_due_at") or status.get("next_due_at") or "-")
    next_due_in_human = _runtime_countdown_label(next_due_at) if next_due_at not in {"", "-"} else str(
        status.get("next_due_in_human") or "-"
    )
    latest_topic = str(selected_latest.get("topic") or status.get("latest_topic") or "-")
    cols = st.columns(6)
    values = [
        ("서비스", "active" if str(status.get("service_status")) == "active" else str(status.get("service_status") or "unknown")),
        ("다음 업로드", str(status.get("next_due_at") or "-")),
        ("남은 시간", str(status.get("next_due_in_human") or "-")),
        ("모니터", str(status.get("monitor_status") or "-")),
        ("최근 주제", str(status.get("latest_topic") or "-")),
    ]
    values = [
        ("Service", "active" if str(status.get("service_status")) == "active" else str(status.get("service_status") or "unknown")),
        ("Studio UI", "active" if str(status.get("studio_service_status")) == "active" else str(status.get("studio_service_status") or "unknown")),
        ("Next Upload", next_due_at),
        ("Remaining", next_due_in_human),
        ("Monitor", str(status.get("monitor_status") or "-")),
        ("Latest Topic", latest_topic),
    ]
    for col, (label, value) in zip(cols, values):
        col.markdown(
            f"<div class='metric-card'><div class='metric-label'>{label}</div><div class='metric-value'>{value}</div></div>",
            unsafe_allow_html=True,
        )

    channel_schedules = status.get("channel_schedules") if isinstance(status.get("channel_schedules"), list) else []
    if selected_channel_id and selected_schedule:
        st.caption(
            f"Selected channel: {selected_schedule.get('display_name', selected_channel_id)} / "
            f"{selected_schedule.get('schedule_label', '-')}"
        )
    if channel_schedules:
        st.dataframe(
            [
                {
                    "채널": item.get("display_name", item.get("channel_id", "")),
                    "일정": item.get("schedule_label", ""),
                    "다음 업로드": item.get("next_due_at", ""),
                    "최근 상태": item.get("last_status", ""),
                    "최근 완료": item.get("last_completed_at", ""),
                }
                for item in channel_schedules
                if isinstance(item, dict)
            ],
            use_container_width=True,
        )

    studio_public_url = str(status.get("studio_public_url") or "").strip()
    if studio_public_url:
        st.caption(f"Server Studio URL: {studio_public_url}")

    upload_link = str(latest_upload.get("path") or "").strip()
    if upload_link:
        st.markdown(f"최근 업로드 링크: {upload_link}")


def _render_manual_tab(store: StudioSettingsStore, settings, profile: ChannelProfile) -> None:
    st.caption("수동 실행은 현재 저장된 채널 설정을 기준으로 동작합니다. 실행 전에 필요한 변경사항은 먼저 저장하세요.")
    if _current_studio_session_source() == "studio-server":
        st.warning(
            "원격 Studio에서는 YouTube OAuth 인증과 채널 선택이 불안정할 수 있습니다. "
            "채널 검증이 계속 실패하면 로컬 PC에서 `python .\\main.py --mode auth-youtube --channel-id <채널ID>`로 "
            "다시 인증한 뒤 `verify-youtube-channel`을 실행하는 편이 더 안정적입니다."
        )
    run_cols = st.columns(6)
    if run_cols[0].button("YouTube 연결", use_container_width=True):
        _persist_local_settings(store, settings, profile)
        _show_pipeline_result(_authorize_youtube(profile.id))
    if run_cols[1].button("채널 검증", use_container_width=True):
        _persist_local_settings(store, settings, profile)
        _show_pipeline_result(_verify_youtube_channel(profile.id))
    if run_cols[2].button("Dry Run", use_container_width=True):
        _persist_local_settings(store, settings, profile)
        _show_pipeline_result(_run_pipeline(profile.id, "dry-run"))
    if run_cols[3].button("Generate + Upload", use_container_width=True):
        _persist_local_settings(store, settings, profile)
        _show_pipeline_result(_run_pipeline(profile.id, "run-once"))
    if run_cols[4].button("Render Only", use_container_width=True):
        _persist_local_settings(store, settings, profile)
        _show_pipeline_result(_run_pipeline(profile.id, "render-only"))
    if run_cols[5].button("Upload Only", use_container_width=True):
        _persist_local_settings(store, settings, profile)
        _show_pipeline_result(_run_pipeline(profile.id, "upload-only"))

    st.markdown("#### YouTube Cleanup")
    st.caption("Delete needs a reconnected token with Manage/Delete scope.")
    cleanup_cols = st.columns([3, 1])
    cleanup_cols[0].text_area(
        "Delete video IDs",
        key="cleanup_video_ids_text",
        height=90,
        help="One YouTube video ID per line, or comma-separated.",
    )
    if cleanup_cols[1].button("Delete Videos", use_container_width=True):
        _persist_local_settings(store, settings, profile)
        _show_pipeline_result(_delete_youtube_videos(profile.id, _split_video_ids(st.session_state.cleanup_video_ids_text)))
    repair_cols = st.columns([3, 1])
    repair_cols[0].caption("Repair Metadata uses the typed video ID list, or the latest uploaded video for this channel when left blank.")
    if repair_cols[1].button("Repair Metadata", use_container_width=True):
        _persist_local_settings(store, settings, profile)
        _show_pipeline_result(_repair_youtube_metadata(profile.id, _split_video_ids(st.session_state.cleanup_video_ids_text)))


def _render_history_tab(profile: ChannelProfile) -> None:
    config = load_config(PROJECT_ROOT, channel_id=profile.id)
    repository = StorageRepository(config)
    runs = repository.list_runs(limit=20, channel_id=profile.id)
    if not runs:
        st.info("아직 실행 기록이 없습니다.")
        return

    st.dataframe(
        [
            {
                "생성시각": item.get("created_at", ""),
                "주제": item.get("topic", {}).get("representative_title", ""),
                "제목": item.get("content", {}).get("video_title", ""),
                "업로드": item.get("artifacts", {}).get("upload", {}).get("path", ""),
                "형식": item.get("content", {}).get("content_format", "short"),
            }
            for item in runs
        ],
        use_container_width=True,
    )


def _load_server_runtime_status(*, force: bool = False) -> dict[str, object]:
    cached = st.session_state.get("_server_runtime_status")
    if cached and not force:
        return cached

    status = fetch_server_runtime_status(PROJECT_ROOT)
    st.session_state._server_runtime_status = status
    st.session_state._server_runtime_status_fetched_at = datetime.now().isoformat(timespec="seconds")
    return status


def _selected_channel_runtime_schedule(status: dict[str, object], channel_id: str) -> dict[str, object]:
    if not channel_id:
        return {}
    channel_schedules = status.get("channel_schedules") if isinstance(status.get("channel_schedules"), list) else []
    for item in channel_schedules:
        if isinstance(item, dict) and str(item.get("channel_id") or "").strip() == channel_id:
            return item
    return {}


def _server_status_available() -> bool:
    return load_remote_target(PROJECT_ROOT).is_configured


def _runtime_countdown_label(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "-"

    current_time = datetime.now(parsed.tzinfo) if parsed.tzinfo is not None else datetime.now()
    remaining = int((parsed - current_time).total_seconds())
    if remaining <= 0:
        return "due now"
    hours, remainder = divmod(remaining, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _seed_form_state(
    profile: ChannelProfile,
    automation: AutomationSettings,
    remote_access: RemoteAccessSettings,
    *,
    force: bool = False,
) -> None:
    if st.session_state.get("_loaded_profile_id") == profile.id and not force:
        return

    st.session_state._loaded_profile_id = profile.id
    for key, value in asdict(profile).items():
        st.session_state[key] = value
    st.session_state.topic_include_keywords_text = "\n".join(profile.topic_include_keywords)
    st.session_state.topic_exclude_keywords_text = "\n".join(profile.topic_exclude_keywords)
    st.session_state.not_for_kids = not profile.made_for_kids
    schedule_times = list(profile.daily_upload_times or ([] if not profile.daily_upload_time else [profile.daily_upload_time]))
    st.session_state.daily_upload_times_text = "\n".join(schedule_times)
    st.session_state.schedule_interval_hours = int(getattr(profile, "schedule_interval_hours", 6) or 6)
    st.session_state.global_default_interval_hours = int(automation.schedule_hours)
    st.session_state.remote_access_enabled = remote_access.enabled
    st.session_state.remote_access_mode = remote_access.mode
    st.session_state.remote_tunnel_name = remote_access.tunnel_name
    st.session_state.remote_hostname = remote_access.hostname
    st.session_state.remote_tunnel_token = remote_access.tunnel_token
    st.session_state.cleanup_video_ids_text = st.session_state.get("cleanup_video_ids_text", "")

def _profile_from_state(profile: ChannelProfile) -> ChannelProfile:
    payload: dict[str, Any] = dict(asdict(profile))
    payload.update(
        {
            "display_name": st.session_state.display_name.strip(),
            "youtube_channel_title": st.session_state.youtube_channel_title.strip(),
            "youtube_channel_id": st.session_state.youtube_channel_id.strip(),
            "preset_key": st.session_state.preset_key,
            "channel_group": preset_by_key(st.session_state.preset_key).group,
            "privacy_status": st.session_state.privacy_status,
            "category_id": st.session_state.category_id.strip(),
            "default_language": st.session_state.default_language.strip(),
            "default_audio_language": st.session_state.default_audio_language.strip(),
            "made_for_kids": not st.session_state.not_for_kids,
            "altered_content_mode": st.session_state.altered_content_mode,
            "manual_contains_synthetic": st.session_state.manual_contains_synthetic,
            "caption_certification_hint": st.session_state.caption_certification_hint.strip() or DEFAULT_CAPTION_CERTIFICATION,
            "use_ai_text": st.session_state.use_ai_text,
            "use_ai_images": st.session_state.use_ai_images,
            "use_ai_metadata": st.session_state.use_ai_metadata,
            "title_prefix": st.session_state.title_prefix.strip(),
            "title_suffix": st.session_state.title_suffix.strip(),
            "call_to_action": st.session_state.call_to_action.strip(),
            "extra_instructions": st.session_state.extra_instructions.strip(),
            "topic_include_keywords": _split_keywords(st.session_state.topic_include_keywords_text),
            "topic_exclude_keywords": _split_keywords(st.session_state.topic_exclude_keywords_text),
            "content_duration_seconds": int(st.session_state.content_duration_seconds),
            "visual_style": st.session_state.visual_style,
            "manual_title": st.session_state.manual_title.strip(),
            "manual_description": st.session_state.manual_description.strip(),
            "manual_thumbnail_path": st.session_state.manual_thumbnail_path.strip(),
            "manual_background_path": st.session_state.manual_background_path.strip(),
            "manual_thumbnail_prompt": st.session_state.manual_thumbnail_prompt.strip(),
            "manual_background_prompt": st.session_state.manual_background_prompt.strip(),
            "youtube_client_secrets_file": st.session_state.youtube_client_secrets_file.strip(),
            "youtube_token_file": stored_youtube_token_file(st.session_state.youtube_token_file.strip(), payload["id"]),
            "schedule_enabled": bool(st.session_state.schedule_enabled),
            "schedule_interval_hours": max(1, int(st.session_state.get("schedule_interval_hours", getattr(profile, "schedule_interval_hours", 6) or 6))),
            "daily_upload_times": _split_schedule_times(st.session_state.daily_upload_times_text),
            "daily_upload_time": "",
            "story_scene_count": max(3, int(st.session_state.story_scene_count)),
            "hook_duration_seconds": max(20, int(st.session_state.hook_duration_seconds)),
            "story_images_per_scene": max(1, int(st.session_state.story_images_per_scene)),
            "burn_in_subtitles": bool(st.session_state.burn_in_subtitles),
            "background_music_path": str(st.session_state.background_music_path or "").strip(),
            "background_music_volume": max(0, min(100, int(st.session_state.background_music_volume))),
            "hook_motion_template": str(st.session_state.hook_motion_template or "dramatic_push").strip() or "dramatic_push",
        }
    )
    return ChannelProfile(**payload)

def _automation_from_state() -> AutomationSettings:
    return AutomationSettings(schedule_hours=max(1, int(st.session_state.get("global_default_interval_hours", 6))))


def _remote_access_from_state() -> RemoteAccessSettings:
    return RemoteAccessSettings(
        enabled=bool(st.session_state.remote_access_enabled),
        mode=str(st.session_state.remote_access_mode or "quick"),
        tunnel_name=str(st.session_state.remote_tunnel_name or DEFAULT_TUNNEL_NAME).strip() or DEFAULT_TUNNEL_NAME,
        hostname=str(st.session_state.remote_hostname or "").strip(),
        tunnel_token=str(st.session_state.remote_tunnel_token or "").strip(),
    )


def _persist_local_settings(store: StudioSettingsStore, settings, profile: ChannelProfile) -> ChannelProfile:
    updated_profile = _profile_from_state(profile)
    settings.channels = [updated_profile if channel.id == profile.id else channel for channel in settings.channels]
    settings.active_channel_id = st.session_state.selected_channel_id
    settings.automation = _automation_from_state()
    settings.remote_access = _remote_access_from_state()
    store.save(settings)
    return updated_profile


def _save_all_settings(store: StudioSettingsStore, settings, profile: ChannelProfile) -> None:
    updated_profile = _persist_local_settings(store, settings, profile)
    persisted_profile = _persisted_profile(store, updated_profile.id)
    server_result = sync_server_settings(PROJECT_ROOT)
    monitor_result = arm_next_run_monitor(PROJECT_ROOT) if server_result["status"] == "success" else {"status": "skipped", "message": ""}
    st.session_state.pop("_server_runtime_status", None)
    st.session_state["_startup_reconcile_result"] = server_result
    if server_result["status"] == "success":
        local_result = uninstall_windows_scheduled_task()
    else:
        local_result = install_windows_scheduled_task(PROJECT_ROOT, minutes=5, start_delay_minutes=1)

    messages = [
        f"채널 설정이 저장되었습니다. 현재 업로드 일정: {_selected_schedule_label()}",
        f"로컬 저장 확인 일정: {_channel_schedule_label(persisted_profile)}",
    ]
    if local_result["status"] in {"success", "missing", "skipped"}:
        messages.append(local_result["message"])
    else:
        messages.append(f"로컬 자동 업로드 예약 작업 처리 실패: {local_result['message']}")

    if server_result["status"] == "success":
        messages.append(server_result["message"])
        if monitor_result.get("status") == "success":
            messages.append(f"다음 업로드 모니터 재설정: {monitor_result.get('next_due_at', '')}")
        st.success("\n\n".join(messages))
    elif server_result["status"] == "pending":
        messages.append(server_result["message"])
        if local_result["status"] == "success":
            messages.append("서버에 바로 반영되지 않아 로컬 Windows 예약 작업 fallback을 5분 간격으로 켰습니다.")
        st.warning("\n\n".join(messages))
    elif server_result["status"] == "skipped":
        messages.append(server_result["message"])
        st.warning("\n\n".join(messages))
    else:
        messages.append(f"서버 반영 실패: {server_result['message']}")
        if local_result["status"] == "success":
            messages.append("서버 복구 전까지는 로컬 Windows 예약 작업 fallback이 5분 간격으로 지정 시간 도달 여부를 확인합니다.")
        st.error("\n\n".join(messages))


def _persisted_profile(store: StudioSettingsStore, channel_id: str) -> ChannelProfile:
    reloaded = store.load()
    for channel in reloaded.channels:
        if channel.id == channel_id:
            return channel
    return reloaded.channels[0]


def _run_pipeline(channel_id: str, mode: str) -> dict[str, Any]:
    config = load_config(PROJECT_ROOT, channel_id=channel_id)
    configure_logging(config.logs_dir, config.log_level)
    pipeline = Pipeline(config)
    if mode == "dry-run":
        result = pipeline.dry_run()
    elif mode == "render-only":
        result = pipeline.render_only()
    elif mode == "upload-only":
        result = pipeline.upload_only()
    else:
        result = pipeline.run_once()
    return result.to_dict()


def _authorize_youtube(channel_id: str) -> dict[str, Any]:
    config = load_config(PROJECT_ROOT, channel_id=channel_id)
    configure_logging(config.logs_dir, config.log_level)
    result = YouTubeUploader(config).authorize()
    payload = result.to_dict()
    if result.status == "created":
        payload.setdefault("extra", {})
        payload["extra"]["server_sync"] = sync_server_settings(PROJECT_ROOT)
    return payload


def _verify_youtube_channel(channel_id: str) -> dict[str, Any]:
    config = load_config(PROJECT_ROOT, channel_id=channel_id)
    configure_logging(config.logs_dir, config.log_level)
    return YouTubeUploader(config).verify_channel_binding().to_dict()


def _delete_youtube_videos(channel_id: str, video_ids: list[str]) -> dict[str, Any]:
    config = load_config(PROJECT_ROOT, channel_id=channel_id)
    configure_logging(config.logs_dir, config.log_level)
    result = YouTubeUploader(config).delete_videos(video_ids)
    payload = result.to_dict()
    if result.status in {"created", "partial"}:
        payload.setdefault("extra", {})
        payload["extra"]["server_sync"] = sync_server_settings(PROJECT_ROOT)
    return payload


def _repair_youtube_metadata(channel_id: str, video_ids: list[str]) -> dict[str, Any]:
    config = load_config(PROJECT_ROOT, channel_id=channel_id)
    configure_logging(config.logs_dir, config.log_level)
    repository = StorageRepository(config)
    latest_run = repository.latest_uploaded_run(channel_id=channel_id)
    if not latest_run:
        return {
            "status": "failed",
            "provider": "youtube-update",
            "message": "No saved run metadata was found for this channel.",
        }

    topic_payload = latest_run.get("topic", {}) if isinstance(latest_run.get("topic", {}), dict) else {}
    topic = RankedTopic(
        normalized_topic=str(topic_payload.get("normalized_topic", "") or ""),
        representative_title=str(topic_payload.get("representative_title", "") or ""),
        score=float(topic_payload.get("score", 0.0) or 0.0),
        sources=[str(item) for item in topic_payload.get("sources", []) if item],
        mentions=[str(item) for item in topic_payload.get("mentions", []) if item],
        keywords=[str(item) for item in topic_payload.get("keywords", []) if item],
    )
    details = [
        TopicDetail(
            title=str(item.get("title", "") or ""),
            summary=str(item.get("summary", "") or ""),
            source=str(item.get("source", "") or ""),
            url=str(item.get("url", "") or "") or None,
            published_at=str(item.get("published_at", "") or "") or None,
        )
        for item in latest_run.get("details_collected", [])
        if isinstance(item, dict)
    ]
    content = ContentGenerator(config).generate(topic, details)

    normalized_ids = [
        YouTubeUploader.extract_video_id(video_id)
        for video_id in video_ids
        if YouTubeUploader.extract_video_id(video_id)
    ]
    if not normalized_ids:
        upload_payload = latest_run.get("artifacts", {}).get("upload", {}) if isinstance(latest_run.get("artifacts", {}), dict) else {}
        upload_extra = upload_payload.get("extra", {}) if isinstance(upload_payload, dict) and isinstance(upload_payload.get("extra", {}), dict) else {}
        for candidate in (upload_extra.get("video_id", ""), upload_payload.get("path", "") if isinstance(upload_payload, dict) else ""):
            extracted = YouTubeUploader.extract_video_id(str(candidate or ""))
            if extracted:
                normalized_ids.append(extracted)
    result = YouTubeUploader(config).update_video_metadata(normalized_ids, content)
    payload = result.to_dict()
    if result.status in {"created", "partial"}:
        payload.setdefault("extra", {})
        payload["extra"]["server_sync"] = sync_server_settings(PROJECT_ROOT)
    return payload


def _show_pipeline_result(payload: dict[str, Any]) -> None:
    provider = str(payload.get("provider") or "").strip()
    status = str(payload.get("status") or "").strip()
    message = str(payload.get("message") or "").strip()
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}

    if message:
        if status == "failed":
            st.error(message)
        elif status in {"partial", "mocked", "skipped"}:
            st.warning(message)
        elif status in {"created", "success"}:
            st.success(message)
        else:
            st.info(message)

    if provider in {"youtube-auth", "youtube-verify"} and extra.get("channel_mismatch"):
        expected_label = _format_channel_label(
            extra.get("expected_channel_title"),
            extra.get("expected_channel_id"),
        )
        authorized_label = _format_channel_label(
            extra.get("authorized_channel_title"),
            extra.get("authorized_channel_id"),
        )
        st.error(f"현재 저장된 토큰 채널은 {authorized_label} 입니다. 설정된 채널은 {expected_label} 입니다.")
        st.markdown("다음 순서로 다시 진행해주세요.")
        st.markdown("1. 해당 채널을 선택한 상태에서 `YouTube 연결`을 다시 실행합니다.")
        st.markdown("2. 구글 승인 화면에서 반드시 원하는 유튜브 채널을 선택합니다.")
        st.markdown("3. 인증 직후 바로 `채널 검증`을 눌러 일치 여부를 확인합니다.")
        if _current_studio_session_source() == "studio-server":
            channel_id = str(st.session_state.get("_loaded_profile_id") or "").strip()
            if channel_id:
                st.info(
                    "원격 Studio에서 계속 실패하면 로컬 PC에서 아래 명령으로 인증/검증하는 편이 더 안정적입니다.\n"
                    f"`python .\\main.py --mode auth-youtube --channel-id {channel_id}`\n"
                    f"`python .\\main.py --mode verify-youtube-channel --channel-id {channel_id}`"
                )

    with st.expander("원본 결과 보기", expanded=status == "failed"):
        st.json(payload)


def _auto_decision(profile: ChannelProfile) -> bool:
    config = load_config(PROJECT_ROOT, channel_id=profile.id)
    return decide_contains_synthetic_media(config)


def _channel_video_profile(profile: ChannelProfile) -> dict[str, str]:
    config = load_config(PROJECT_ROOT, channel_id=profile.id)
    is_landscape = config.render.width > config.render.height
    return {
        "label": "16:9 Longform" if is_landscape else "9:16 Shorts",
        "size": f"{config.render.width} x {config.render.height}",
    }


def _youtube_auth_status(profile: ChannelProfile) -> dict[str, Any]:
    config = load_config(PROJECT_ROOT, channel_id=profile.id)
    client_secrets = Path(config.youtube.client_secrets_file) if config.youtube.client_secrets_file else None
    token_path = Path(
        resolve_youtube_token_file(
            st.session_state.get("youtube_token_file", profile.youtube_token_file),
            profile.id,
            PROJECT_ROOT,
        )
    )
    status = {
        "client_secrets_exists": bool(client_secrets and client_secrets.exists()),
        "token_exists": token_path.exists(),
        "token_path": str(token_path),
        "scopes": [],
        "has_upload_scope": False,
        "has_manage_scope": False,
        "has_read_scope": False,
    }
    if token_path.exists():
        try:
            payload = json.loads(token_path.read_text(encoding="utf-8"))
            scopes = [str(scope) for scope in payload.get("scopes", []) if scope]
        except (OSError, json.JSONDecodeError):
            scopes = []
        granted = set(scopes)
        status["scopes"] = scopes
        status["has_upload_scope"] = bool(UPLOAD_SCOPE in granted or MANAGE_SCOPE in granted or FULL_SCOPE in granted)
        status["has_manage_scope"] = bool(MANAGE_SCOPE in granted or FULL_SCOPE in granted)
        status["has_read_scope"] = bool(READONLY_SCOPE in granted or MANAGE_SCOPE in granted or FULL_SCOPE in granted)
    return status


def _split_video_ids(raw: str) -> list[str]:
    cleaned = str(raw or "").replace(",", "\n")
    return [line.strip() for line in cleaned.splitlines() if line.strip()]


def _current_studio_session_source() -> str:
    payload = read_studio_session(PROJECT_ROOT)
    return str(payload.get("source") or "").strip().lower()


def _format_channel_label(title: object, channel_id: object) -> str:
    name = str(title or "").strip()
    value = str(channel_id or "").strip()
    if name and value:
        return f'"{name}" ({value})'
    if name:
        return f'"{name}"'
    if value:
        return f"({value})"
    return "알 수 없는 채널"


def _apply_preset_defaults_to_state(preset_key: str) -> None:
    preset = preset_by_key(preset_key)
    st.session_state.category_id = preset.category_id
    st.session_state.title_prefix = preset.title_prefix
    st.session_state.title_suffix = preset.title_suffix
    st.session_state.call_to_action = preset.call_to_action
    st.session_state.visual_style = preset.visual_style
    st.session_state.content_duration_seconds = preset.content_duration_seconds
    st.session_state.story_scene_count = preset.scene_count or 7
    st.session_state.hook_duration_seconds = preset.hook_duration_seconds or 40
    st.session_state.story_images_per_scene = 3
    st.session_state.burn_in_subtitles = True
    st.session_state.background_music_path = ""
    st.session_state.background_music_volume = 18
    st.session_state.hook_motion_template = "dramatic_push"
    st.session_state.topic_include_keywords_text = "\n".join(preset.topic_include_keywords)
    st.session_state.topic_exclude_keywords_text = "\n".join(preset.topic_exclude_keywords)
    if preset.key in {"economy_news", "welfare_news"}:
        st.session_state.schedule_enabled = True
        st.session_state.daily_upload_times_text = "\n".join(DEFAULT_SHORTS_UPLOAD_TIMES)
        st.session_state.schedule_interval_hours = 6
    elif preset.key == "quotes_daily":
        st.session_state.schedule_enabled = False
        st.session_state.daily_upload_times_text = ""
    elif preset.key == "senior_story_longform":
        st.session_state.schedule_enabled = True
        st.session_state.daily_upload_times_text = ""
        st.session_state.schedule_interval_hours = 168


def _is_story_channel() -> bool:
    return preset_by_key(st.session_state.preset_key).content_format == "longform_story"


def _selected_schedule_label() -> str:
    if not bool(st.session_state.get("schedule_enabled", True)):
        return "자동 업로드 꺼짐"
    daily_times = _split_schedule_times(st.session_state.get("daily_upload_times_text", ""))
    if daily_times:
        return f"매일 {', '.join(daily_times)}"
    return f"{int(st.session_state.get('schedule_interval_hours', 6))}시간마다"


def _schedule_state_changed(profile: ChannelProfile) -> bool:
    saved_enabled = bool(getattr(profile, "schedule_enabled", True))
    saved_daily_times = list(
        getattr(profile, "daily_upload_times", []) or ([] if not profile.daily_upload_time else [profile.daily_upload_time])
    )
    current_enabled = bool(st.session_state.get("schedule_enabled", True))
    current_daily_times = _split_schedule_times(st.session_state.get("daily_upload_times_text", ""))
    if saved_enabled != current_enabled:
        return True
    if saved_daily_times != current_daily_times:
        return True
    if current_daily_times:
        return False
    saved_interval = int(getattr(profile, "schedule_interval_hours", 6) or 6)
    current_interval = int(st.session_state.get("schedule_interval_hours", 6) or 6)
    return saved_interval != current_interval


def _channel_schedule_label(channel: ChannelProfile) -> str:
    if not bool(getattr(channel, "schedule_enabled", True)):
        return "자동 업로드 꺼짐"
    daily_times = list(getattr(channel, "daily_upload_times", []) or ([] if not channel.daily_upload_time else [channel.daily_upload_time]))
    return f"매일 {', '.join(daily_times)}" if daily_times else f"{channel.schedule_interval_hours}시간마다"

def _split_schedule_times(text: str) -> list[str]:
    values: list[str] = []
    for raw in str(text or "").replace(",", "\n").splitlines():
        value = str(raw).strip()
        if not value:
            continue
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError:
            continue
        normalized = parsed.strftime("%H:%M")
        if normalized not in values:
            values.append(normalized)
    return sorted(values)

def _split_keywords(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").replace(",", "\n").splitlines() if line.strip()]


def _channel_label(channels: list[ChannelProfile], channel_id: str) -> str:
    channel = _find_channel(channels, channel_id)
    preset = preset_by_key(channel.preset_key)
    return f"{channel.display_name} · {preset.label}"


def _channel_index(channels: list[ChannelProfile], channel_id: str) -> int:
    for index, channel in enumerate(channels):
        if channel.id == channel_id:
            return index
    return 0


def _find_channel(channels: list[ChannelProfile], channel_id: str) -> ChannelProfile:
    for channel in channels:
        if channel.id == channel_id:
            return channel
    return channels[0]


if __name__ == "__main__":
    main()
