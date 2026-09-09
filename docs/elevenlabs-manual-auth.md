# 手动提取 ElevenLabs 网页凭证

本文说明如何从浏览器登录态中，手工取出本项目使用的 **网页 Firebase 授权**，并写成与 `eleven_auths/*.json` 相同结构的数据。

> 你要的是网页登录凭证（`Authorization: Bearer <Firebase ID Token>`），**不是**开发者控制台里的 `xi-api-key` / `sk_...`。

---

## 1. 需要哪些字段

| 字段 | 必填 | 来源 | 用途 |
| --- | --- | --- | --- |
| `access_token` / `id_token` | 是 | Firebase ID Token | 调 `api.us.elevenlabs.io` 的 Bearer |
| `refresh_token` | 强烈建议 | Firebase Refresh Token | ID Token 约 1 小时过期后刷新 |
| `email` | 是 | Cookie / Firebase 用户 | 标识账号 |
| `user_id` | 建议 | Cookie `xi_website_user` | ElevenLabs 用户 ID |
| `workspace_id` | 建议 | Cookie `xi_website_user` | 工作区 ID |
| `auth_account_id` | 建议 | Firebase `uid` | Firebase 账号 UID |
| `firebase_api_key` | 刷新时需要 | 前端公开配置 | 调 Google Secure Token 接口 |
| `api_base` | 固定 | — | `https://api.us.elevenlabs.io` |

项目内置的网页 Firebase API Key（公开配置，可写进 JSON）：

```text
AIzaSyBSsRE_1Os04-bxpd5JTLIniy3UK4OqKys
```

---

## 2. 准备工作

1. 用正常浏览器打开 [https://elevenlabs.io/app/sign-in](https://elevenlabs.io/app/sign-in) 并登录。
2. 若跳到 `/app/onboarding`，先把引导问卷走完（姓名 / 用途等），直到能进入 `/app/home` 或 Speech Synthesis。
3. 打开开发者工具：`F12` / `Cmd+Option+I`。

建议同时开两个面板：

- **Application（应用程序）**：看 Cookie / IndexedDB
- **Network（网络）**：看带 `Authorization` 的请求

---

## 3. 方法 A：从 Network 抄当前 Bearer（最快）

1. 打开 Network，过滤 `user` 或 `text-to-speech`。
2. 在页面里随便点一下会发 API 的操作（例如打开语音合成、刷新首页）。
3. 找到请求：
   - `https://api.us.elevenlabs.io/v1/user`
   - 或 `https://api.us.elevenlabs.io/v1/text-to-speech/...`
4. 看 Request Headers：

```http
Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...
Origin: https://elevenlabs.io
Referer: https://elevenlabs.io/app/...
```

5. 复制 `Bearer` 后面整段，即为 `access_token` / `id_token`。

注意：

- 这只是**当前短时 ID Token**，通常约 1 小时过期。
- Network 里一般**看不到** `refresh_token`，还需要方法 B。

---

## 4. 方法 B：从 IndexedDB 取 ID Token + Refresh Token（推荐）

ElevenLabs 使用 Firebase Auth，持久化在浏览器 IndexedDB。

1. Application → **IndexedDB** → `firebaseLocalStorageDb` → `firebaseLocalStorage`
2. 打开其中一条记录（`fbase_key` 类似 `firebase:authUser:...`）
3. 展开 `value` → `stsTokenManager`：

| IndexedDB 路径 | 对应字段 |
| --- | --- |
| `value.stsTokenManager.accessToken` | `access_token` / `id_token` |
| `value.stsTokenManager.refreshToken` | `refresh_token` |
| `value.uid` | `auth_account_id` |
| `value.email` | `email` |

也可在 Console 执行（登录状态下）：

```js
(async () => {
  const db = await new Promise((resolve, reject) => {
    const req = indexedDB.open("firebaseLocalStorageDb");
    req.onerror = () => reject(req.error);
    req.onsuccess = () => resolve(req.result);
  });
  const rows = await new Promise((resolve, reject) => {
    const tx = db.transaction("firebaseLocalStorage", "readonly");
    const req = tx.objectStore("firebaseLocalStorage").getAll();
    req.onerror = () => reject(req.error);
    req.onsuccess = () => resolve(req.result || []);
  });
  db.close();
  for (const row of rows) {
    const v = row && row.value;
    const sts = v && v.stsTokenManager;
    if (sts && sts.accessToken) {
      console.log({
        email: v.email,
        uid: v.uid,
        accessToken: sts.accessToken,
        refreshToken: sts.refreshToken,
      });
      return;
    }
  }
  console.log("未找到 firebase authUser");
})();
```

---

## 5. 方法 C：从 Cookie 取用户元数据

Application → Cookies → `https://elevenlabs.io`

查找 Cookie 名：`xi_website_user`（值是 URL 编码的 JSON）。

解码后大致包含：

```json
{
  "user_id": "user_...",
  "workspace_id": "...",
  "auth_account_id": "...",
  "email": "you@example.com"
}
```

对应写入授权文件：

- `user_id`
- `workspace_id`
- `auth_account_id`
- `email`

---

## 6. 组装成本项目 JSON

保存为例如 `eleven_auths/elevenlabs-you@example.com.json`：

```json
{
  "type": "elevenlabs",
  "auth_kind": "web_firebase_id_token",
  "access_token": "<Firebase ID Token>",
  "id_token": "<Firebase ID Token>",
  "refresh_token": "<Firebase Refresh Token>",
  "email": "you@example.com",
  "user_id": "user_...",
  "workspace_id": "...",
  "auth_account_id": "<firebase uid>",
  "project_id": "<workspace_id 或 user_id>",
  "api_base": "https://api.us.elevenlabs.io",
  "firebase_api_key": "AIzaSyBSsRE_1Os04-bxpd5JTLIniy3UK4OqKys",
  "is_onboarding_completed": true,
  "request_headers": {
    "Authorization": "Bearer <access_token>",
    "Content-Type": "application/json",
    "Origin": "https://elevenlabs.io",
    "Referer": "https://elevenlabs.io/app/speech-synthesis",
    "x-generation-surface": "Speech Synthesis",
    "x-generation-actor": "User"
  }
}
```

`project_id` 可填 `workspace_id`；若没有 workspace，则用 `user_id` / `auth_account_id`。

---

## 7. 刷新过期的 ID Token

当 `access_token` 过期时，用 `refresh_token` 调 Google Secure Token：

```bash
curl -s "https://securetoken.googleapis.com/v1/token?key=AIzaSyBSsRE_1Os04-bxpd5JTLIniy3UK4OqKys" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Origin: https://elevenlabs.io" \
  -H "Referer: https://elevenlabs.io/" \
  --data-urlencode "grant_type=refresh_token" \
  --data-urlencode "refresh_token=<你的 refresh_token>"
```

成功响应里：

- `id_token` → 新的 `access_token`
- `refresh_token` → 可能轮换，建议回写文件

---

## 8. 用网页凭证调用接口

### 查用户

```bash
curl -s https://api.us.elevenlabs.io/v1/user \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -H "Origin: https://elevenlabs.io" \
  -H "Referer: https://elevenlabs.io/app/home" \
  -H "x-generation-surface: Speech Synthesis" \
  -H "x-generation-actor: User"
```

关注：

- `is_onboarding_completed` 应为 `true`
- `subscription` / Free Tier 状态

### 文本转语音（网页同款头）

```bash
curl -s -D - -o /tmp/el.mp3 \
  "https://api.us.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM" \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -H "Accept: audio/mpeg" \
  -H "Origin: https://elevenlabs.io" \
  -H "Referer: https://elevenlabs.io/app/speech-synthesis" \
  -H "x-generation-surface: Speech Synthesis" \
  -H "x-generation-actor: User" \
  --data '{"text":"hello","model_id":"eleven_multilingual_v2"}'
```

若返回：

```json
{"detail":{"status":"detected_unusual_activity","message":"..."}}
```

表示该免费号已被风控禁用 TTS，常见于代理/VPN、批量免费号。这与“有没有抄对 token”可能无关——同一 token 在网页端点 TTS 也可能随后不可用。

---

## 9. 用项目脚本探测

仓库提供：

```bash
.venv/bin/python scripts/verify_eleven_web_auth.py \
  --limit 1 \
  --proxy 'host:port:user:pass'
```

会依次：刷新 token → `GET /v1/user` → 探测网页 TTS。

---

## 10. 常见误区

| 误区 | 正确做法 |
| --- | --- |
| 去 Profile → API Key 复制 `sk_...` | 那是开发者 Key；本项目要的是网页 Firebase Bearer |
| 只存 ID Token、不存 refresh | 约 1 小时后全部失效 |
| 不带 `Origin` / `Referer` / generation 头 | 部分接口行为与网页不一致 |
| 未完成 onboarding 就调业务接口 | 先完成 `/app/onboarding` |
| 把 `xi_website_user` 整段当 Bearer | Cookie 只含用户元数据，不能当 Authorization |

---

## 11. 最小检查清单

- [ ] 已登录且 onboarding 完成
- [ ] 已拿到 `access_token`（ID Token）
- [ ] 已拿到 `refresh_token`
- [ ] 已拿到 `email` + `user_id` / `workspace_id`（可选但建议）
- [ ] `GET /v1/user` 返回 200
- [ ] TTS 若 401 `detected_unusual_activity`，优先怀疑账号/出口风控，而不是字段抄错
