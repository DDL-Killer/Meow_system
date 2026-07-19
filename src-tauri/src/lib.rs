use tauri::Manager;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
struct ProxyResponse { status: u16, body: String }

const SERVER: &str = "http://localhost:8000";  // 部署时改为你的服务器地址

macro_rules! req {
    ($method:ident, $url:expr, $body:expr, $token:expr) => {{
        let client = reqwest::Client::new();
        let mut r = client.$method($url).header("Content-Type", "application/json");
        if !$token.is_empty() { r = r.header("Authorization", format!("Bearer {}", $token)); }
        if !$body.is_empty() { r = r.body($body.to_string()); }
        r.send().await.map_err(|e| format!("Err: {}", e))
    }};
}

#[tauri::command]
async fn api_get(url: String, token: String) -> Result<ProxyResponse, String> {
    let u = format!("{}{}", SERVER, url);
    let resp = req!(get, &u, "", &token)?.text().await.unwrap_or_default();
    Ok(ProxyResponse { status: 200, body: resp })
}

#[tauri::command]
async fn api_post(url: String, body_str: String, token: String) -> Result<ProxyResponse, String> {
    let u = format!("{}{}", SERVER, url);
    let resp = req!(post, &u, &body_str, &token)?;
    Ok(ProxyResponse { status: resp.status().as_u16(), body: resp.text().await.unwrap_or_default() })
}

#[tauri::command]
async fn api_patch(url: String, body_str: String, token: String) -> Result<ProxyResponse, String> {
    let u = format!("{}{}", SERVER, url);
    let resp = req!(patch, &u, &body_str, &token)?;
    Ok(ProxyResponse { status: resp.status().as_u16(), body: resp.text().await.unwrap_or_default() })
}

#[tauri::command]
async fn api_delete(url: String, token: String) -> Result<ProxyResponse, String> {
    let u = format!("{}{}", SERVER, url);
    let resp = req!(delete, &u, "", &token)?;
    Ok(ProxyResponse { status: resp.status().as_u16(), body: resp.text().await.unwrap_or_default() })
}

#[tauri::command]
async fn api_upload(url: String, file_data_b64: String, filename: String, token: String) -> Result<ProxyResponse, String> {
    use base64::Engine;
    let u = format!("{}{}", SERVER, url);
    let client = reqwest::Client::new();
    let file_data = base64::engine::general_purpose::STANDARD.decode(&file_data_b64).map_err(|e| format!("base64: {}", e))?;
    let mime = if filename.ends_with(".mp4") { "audio/mp4" } else if filename.ends_with(".aac") { "audio/aac" } else { "audio/webm" };
    let part = reqwest::multipart::Part::bytes(file_data)
        .file_name(filename)
        .mime_str(mime)
        .map_err(|e| format!("mime: {}", e))?;
    let form = reqwest::multipart::Form::new().part("file", part);
    let mut req = client.post(&u).multipart(form);
    if !token.is_empty() { req = req.header("Authorization", format!("Bearer {}", token)); }
    let resp = req.send().await.map_err(|e| format!("Err: {}", e))?;
    Ok(ProxyResponse { status: resp.status().as_u16(), body: resp.text().await.unwrap_or_default() })
}

#[tauri::command]
async fn api_get_audio(url: String, token: String) -> Result<Vec<u8>, String> {
    let u = if token.is_empty() {
        format!("{}{}", SERVER, url)
    } else {
        format!("{}{}?token={}", SERVER, url, token)
    };
    let client = reqwest::Client::new();
    let resp = client.get(&u).send().await.map_err(|e| format!("Err: {}", e))?;
    resp.bytes().await.map_err(|e| format!("Err: {}", e)).map(|b| b.to_vec())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![api_get, api_post, api_patch, api_delete, api_upload, api_get_audio])
        .setup(|app| {
            #[cfg(desktop)]
            {
                use tauri::tray::{TrayIconBuilder, MouseButton, MouseButtonState, TrayIconEvent};
                use tauri::menu::{MenuBuilder, MenuItemBuilder};
                let show = MenuItemBuilder::with_id("show", "显示道场").build(app).unwrap();
                let quit = MenuItemBuilder::with_id("quit", "退出").build(app).unwrap();
                let _tray = TrayIconBuilder::new()
                    .menu(&MenuBuilder::new(app).item(&show).item(&quit).build().unwrap())
                    .on_menu_event(|app, event| match event.id().as_ref() {
                        "show" => { if let Some(w) = app.get_webview_window("main") { let _ = w.show(); let _ = w.set_focus(); } }
                        "quit" => app.exit(0), _ => {}
                    })
                    .on_tray_icon_event(|tray, event| {
                        if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                            if let Some(w) = tray.app_handle().get_webview_window("main") { let _ = w.show(); let _ = w.set_focus(); }
                        }
                    })
                    .build(app)?;
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(handle_run_event);
}

#[cfg(desktop)]
fn handle_run_event(app: &tauri::AppHandle, event: tauri::RunEvent) {
    if let tauri::RunEvent::Reopen { .. } = event {
        if let Some(w) = app.get_webview_window("main") {
            let _ = w.show();
            let _ = w.set_focus();
        }
    }
}

#[cfg(mobile)]
fn handle_run_event(_app: &tauri::AppHandle, _event: tauri::RunEvent) {}
