// Оболочка К+: окно на системном WebView плюс сайдкар с агентом на Python.
//
// Rust здесь намеренно тонкий. Вся работа — поиск по К+, разговор с моделью,
// сборка документов — осталась в Python, который уже написан и работает.
// Оболочка делает ровно три вещи: поднимает сайдкар, сообщает окну его порт
// и гасит сайдкар при выходе, чтобы ничего не осталось висеть.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::sync::Mutex;

use tauri::{Manager, RunEvent, State};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

struct Api {
    port: u16,
}

/// Сайдкар держим здесь, иначе Rust уронит его сразу после запуска.
struct Sidecar(Mutex<Option<CommandChild>>);

/// Окно спрашивает порт при старте: он каждый раз новый.
#[tauri::command]
fn api_port(api: State<Api>) -> u16 {
    api.port
}

/// Папка программы: та, в которой лежит сам .app.
///
/// Программа переносимая — данные живут рядом с приложением, а не в
/// домашней папке пользователя. Считаем путь от .app, а не от файла
/// внутри него, иначе документы пропали бы при следующей сборке.
fn base_dir() -> Option<std::path::PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let mut current = exe.as_path();
    while let Some(parent) = current.parent() {
        if parent.extension().is_some_and(|e| e == "app") {
            return parent.parent().map(|p| p.to_path_buf());
        }
        current = parent;
    }
    exe.parent().map(|p| p.to_path_buf())
}

/// Кнопка «Документы» в шапке.
#[tauri::command]
fn open_documents() {
    if let Some(base) = base_dir() {
        let path = base.join("Документы");
        let _ = std::fs::create_dir_all(&path);
        let _ = std::process::Command::new("open").arg(path).spawn();
    }
}

/// Свободный порт у системы. Фиксированный брать нельзя: он может быть занят
/// чем угодно, и тогда приложение молча не запустится.
fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .ok()
        .and_then(|l| l.local_addr().ok())
        .map(|a| a.port())
        .unwrap_or(8787)
}

fn main() {
    let port = free_port();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Api { port })
        .manage(Sidecar(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![api_port, open_documents])
        .setup(move |app| {
            let command = app
                .shell()
                .sidecar("kplus-core")?
                .args(["--port", &port.to_string()]);
            let (_events, child) = command.spawn()?;
            *app.state::<Sidecar>().0.lock().unwrap() = Some(child);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("не удалось собрать приложение")
        .run(|app, event| {
            // Закрыли окно — гасим сайдкар. Окно браузера при этом остаётся
            // жить намеренно: в нём хранится вход в К+.
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                if let Some(child) = app.state::<Sidecar>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
