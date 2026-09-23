"""Chinese presentation of existing audit events; stored event codes stay stable."""

import json

from src.core import accounts as a, permissions as p

ACTION_NAMES = {
    "kb_index_rebuild": "重建知识库索引", "kb_index_cancel": "取消索引重建",
    "personal_preferences_saved": "修改个人偏好",
    "personal_preferences_enabled": "启用个人偏好",
    "personal_preferences_disabled": "停用个人偏好",
    "user_created": "创建账号", "user_updated": "修改账号",
    "user_forced_logout": "强制退出登录", "password_changed": "修改密码",
    "local_password_reset": "本地重置密码",
    "role_created": "创建角色", "role_updated": "修改角色", "role_deleted": "删除角色",
    "provider_saved": "保存 API 配置", "provider_deleted": "删除 API 配置",
    "resource_grant_allow": "允许资源访问", "resource_grant_deny": "禁止资源访问",
    "resource_grant_remove": "移除资源授权", "kb_policy_updated": "修改知识库属性",
    "kb_export": "导出知识库", "kb_file_download": "下载知识库文件",
    "upload_started": "开始上传文档", "system_configuration_changed": "修改系统配置",
    "audit_cleared": "清空操作日志",
}
FIELD_NAMES = {
    "id": "对象", "before": "变更前权限", "after": "变更后权限", "permissions": "权限",
    "enabled": "启用状态", "disabled": "账号状态", "changes": "变更内容",
    "display_name": "显示名称", "note": "备注", "role_ids": "分配角色",
    "password_reset": "重置密码", "kind": "资源类型", "resource": "资源",
    "subject_type": "授权对象类型", "subject_id": "授权对象", "effect": "授权规则",
    "actions": "资源权限", "scope": "共享范围", "owner_id": "所有者",
    "name": "名称", "description": "说明",
}
ACTION_PERMISSIONS = {
    "query": "查询 / 提问", "edit": "编辑文档", "manage": "管理及授权",
    "export": "导出知识库", "download": "下载原文件", "use": "调用 API",
}
SETTINGS_NAMES = {
    "log_level": "系统日志级别", "system_prompt": "系统提示词",
    "llm_timeouts": "AI 请求超时", "ai_summary": "AI 摘要", "default_kb": "默认知识库",
    "llm_providers": "AI 服务配置",
}


def present_audit(records):
    # Resolve identifiers without reading conversations, document contents or API secrets.
    names = {
        "user": {r["id"]: r["username"] for r in a.rows("SELECT id,username FROM auth_users")},
        "role": {r["id"]: r["name"] for r in a.rows("SELECT id,name FROM permission_roles")},
        "kb": {r["id"]: r["name"] for r in a.rows("SELECT id,name FROM kbs")},
        "api": {r["id"]: r["name"] for r in a.rows("SELECT id,name FROM auth_providers")},
    }

    def ref(kind, value):
        value = str(value)
        name = names.get(kind, {}).get(value)
        return f"{name}（编号：{value}）" if name else f"编号：{value}（对象已删除或不可用）"

    def field_value(key, value, data, kind):
        if key == "id":
            return ref(kind, value)
        if key == "resource":
            return ref(data.get("kind", kind), value)
        if key == "subject_id":
            return ref(data.get("subject_type", "user"), value)
        if key == "owner_id":
            return ref("user", value)
        if key == "role_ids" and isinstance(value, list):
            return "、".join(ref("role", x) for x in value) or "无"
        if key in ("before", "after", "permissions", "actions") and isinstance(value, list):
            return "、".join(p.CATALOG[x][1] if x in p.CATALOG else ACTION_PERMISSIONS.get(x, x) for x in value) or "无"
        if key == "enabled":
            return "启用" if value else "停用"
        if key == "disabled":
            return "停用" if value else "启用"
        enums = {
            "kind": {"kb": "知识库", "api": "AI API"},
            "subject_type": {"user": "用户", "role": "角色"},
            "effect": {"allow": "允许", "deny": "明确禁止", "remove": "移除授权"},
            "scope": {"private": "私有", "team": "团队共享", "personal": "个人私有"},
        }
        if key in enums and isinstance(value, str):
            return enums[key].get(value, value)
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, dict):
            return "；".join(f"{FIELD_NAMES.get(k, '其他字段（' + k + '）')}：{field_value(k, v, value, kind)}" for k, v in value.items())
        if isinstance(value, list):
            return "、".join(map(str, value)) or "无"
        return str(value) if value is not None and value != "" else "无"

    result = []
    for record in records:
        action, target = record["action"], record["target"] or ""
        kind = ("role" if action.startswith("role_") else "api" if action.startswith("provider_")
                else "kb" if action.startswith("kb_") else "user")
        try:
            data = json.loads(target)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            details = [f"{FIELD_NAMES.get(k, '其他字段（' + k + '）')}：{field_value(k, v, data, kind)}" for k, v in data.items()]
        elif action == "system_configuration_changed":
            method, _, path = target.partition(" ")
            setting = path.removeprefix("/api/v1/settings/").split("/")[0]
            label = "向量模型设置" if path.startswith("/api/v1/embedding/") else SETTINGS_NAMES.get(setting, "系统设置")
            details = [f"配置项：{label}", f"操作：{ {'POST': '执行', 'PUT': '更新', 'PATCH': '更新', 'DELETE': '删除'}.get(method, method)}", f"接口：{path}"]
        elif action == "kb_file_download":
            kid, _, fid = target.partition(":")
            details = [f"知识库：{ref('kb', kid)}", f"文件编号：{fid}"]
        elif action == "upload_started":
            details = [f"上传任务编号：{target}"]
        elif action == "audit_cleared":
            details = ["已清空此前的操作日志，系统日志未受影响。"]
        elif target and action in ACTION_NAMES:
            details = [f"{ {'user': '用户', 'role': '角色', 'kb': '知识库', 'api': 'API 配置'}[kind]}：{ref(kind, target)}"]
        else:
            details = [f"操作详情：{target}"] if target else []
        result.append({**record, "action_label": ACTION_NAMES.get(action, f"其他操作（{action}）"), "details": details})
    return result
