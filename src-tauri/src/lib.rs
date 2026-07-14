use tauri::Manager;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
struct ProxyResponse { status: u16, body: String }

const SERVER: &str = "http://localhost:8000";

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![api_get, api_post, api_patch, api_delete])
        .setup(|app| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.eval(&format!("window.location.replace('{}')", SERVER));
            }
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
            if let tauri::WindowEvent::CloseRequested { api, .. } = event { let _ = window.hide(); api.prevent_close(); }
        })
        .run(tauri::generate_context!())
        .expect("error");
}
