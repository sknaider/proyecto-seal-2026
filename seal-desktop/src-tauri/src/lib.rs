use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// Open a channel's original web app in a separate webview window.
///
/// Strategy adopted from OpenHuman (doc 20): embed `web.whatsapp.com`,
/// `web.telegram.org`, etc. directly in a Tauri secondary WebView so the
/// user sees the original site (and trusts it), with cookies isolated per
/// channel via a unique label.
///
/// Allowed URLs are limited to a hard-coded whitelist — caller cannot pass
/// arbitrary URLs.
#[tauri::command]
fn open_channel_webview(app: tauri::AppHandle, channel: String) -> Result<String, String> {
    let url = match channel.as_str() {
        "whatsapp"     => "https://web.whatsapp.com",
        "telegram"     => "https://web.telegram.org",
        "slack"        => "https://app.slack.com",
        "discord"      => "https://discord.com/app",
        "gmail"        => "https://mail.google.com",
        "google_meet"  => "https://meet.google.com",
        "zoom"         => "https://zoom.us",
        "linkedin"     => "https://www.linkedin.com/messaging/",
        _ => return Err(format!("canal no permitido: {}", channel)),
    };

    let label = format!("channel-{}", channel);
    let title = match channel.as_str() {
        "whatsapp" => "SEAL · WhatsApp",
        "telegram" => "SEAL · Telegram",
        "slack" => "SEAL · Slack",
        "discord" => "SEAL · Discord",
        "gmail" => "SEAL · Gmail",
        "google_meet" => "SEAL · Google Meet",
        "zoom" => "SEAL · Zoom",
        "linkedin" => "SEAL · LinkedIn",
        _ => "SEAL channel",
    };

    if let Some(existing) = app.get_webview_window(&label) {
        let _ = existing.set_focus();
        return Ok(format!("focused {}", label));
    }

    let parsed = url::Url::parse(url).map_err(|e| format!("bad url: {}", e))?;
    let builder = WebviewWindowBuilder::new(&app, &label, WebviewUrl::External(parsed))
        .title(title)
        .inner_size(1100.0, 800.0)
        .min_inner_size(720.0, 540.0);

    builder.build().map_err(|e| format!("no se pudo abrir webview: {}", e))?;
    Ok(format!("opened {}", label))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_http::init())
        .invoke_handler(tauri::generate_handler![open_channel_webview])
        .setup(|app| {
            #[cfg(debug_assertions)]
            {
                let window = app.get_webview_window("main").unwrap();
                window.open_devtools();
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running SEAL Studio");
}
