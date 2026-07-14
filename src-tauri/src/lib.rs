use tauri::Manager;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
struct ProxyResponse { status: u16, body: String }

async fn make_request(url: &str, method: &str, body: &str, token: &str) -> Result<ProxyResponse, String> {
    let full_url = format!("http://localhost:8000{}", url);
    let client = reqwest::Client::new();
    let mut req = match method {
        "POST" => client.post(&full_url).header("Content-Type", "application/json").body(body.to_string()),
        "PATCH" => client.patch(&full_url).header("Content-Type", "application/json").body(body.to_string()),
        "DELETE" => client.delete(&full_url),
        _ => client.get(&full_url),
    };
    if !token.is_empty() {
        req = req.header("Authorization", format!("Bearer {}", token));
    }
    let resp = req.send().await.map_err(|e| format!("Failed: {}", e))?;
    Ok(ProxyResponse { status: resp.status().as_u16(), body: resp.text().await.unwrap_or_default() })
}

#[tauri::command]
async fn api_get(url: String, token: String) -> Result<ProxyResponse, String> { make_request(&url, "GET", "", &token).await }
#[tauri::command]
async fn api_post(url: String, body_str: String, token: String) -> Result<ProxyResponse, String> { make_request(&url, "POST", &body_str, &token).await }
#[tauri::command]
async fn api_patch(url: String, body_str: String, token: String) -> Result<ProxyResponse, String> { make_request(&url, "PATCH", &body_str, &token).await }
#[tauri::command]
async fn api_delete(url: String, token: String) -> Result<ProxyResponse, String> { make_request(&url, "DELETE", "", &token).await }

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![api_get, api_post, api_patch, api_delete])
        .setup(|app| {
            #[cfg(desktop)]
            {
                use tauri::tray::{TrayIconBuilder, MouseButton, MouseButtonState, TrayIconEvent};
                use tauri::menu::{MenuBuilder, MenuItemBuilder};

                let show = MenuItemBuilder::with_id("show", "显示道场").build(app).unwrap();
                let quit = MenuItemBuilder::with_id("quit", "退出").build(app).unwrap();
                let menu = MenuBuilder::new(app).item(&show).item(&quit).build().unwrap();

                let _tray = TrayIconBuilder::new()
                    .menu(&menu)
                    .on_menu_event(|app, event| match event.id().as_ref() {
                        "show" => {
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.show(); let _ = w.set_focus();
                            }
                        }
                        "quit" => app.exit(0),
                        _ => {}
                    })
                    .on_tray_icon_event(|tray, event| {
                        if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                            if let Some(w) = tray.app_handle().get_webview_window("main") {
                                let _ = w.show(); let _ = w.set_focus();
                            }
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
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
