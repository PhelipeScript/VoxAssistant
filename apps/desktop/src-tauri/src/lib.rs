use serde::{Deserialize, Serialize};
use std::process::Stdio;
use tauri::{Emitter, Manager, State};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::ActivationPolicy;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;
use tokio::sync::mpsc;
use std::env;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::sync::Mutex;
use tts::Tts;

mod audio;
mod db;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct PipelineMessage {
    #[serde(rename = "type")]
    pub msg_type: String,
    pub payload: serde_json::Value,
}

pub struct PipelineState {
    pub tx: mpsc::Sender<PipelineMessage>,
}

pub struct AudioState {
    pub is_recording: Arc<AtomicBool>, 
}

pub struct AppState {
    pub db_path: String,
}

// NOVO: Estado para guardar o motor de voz na memória
pub struct TtsState {
    pub engine: Mutex<Tts>,
}

fn play_mac_sound(sound_name: &str) {
    let sound_path = format!("/System/Library/Sounds/{}.aiff", sound_name);
    std::thread::spawn(move || {
        let _ = std::process::Command::new("afplay")
            .arg(sound_path)
            .output();
    });
}

fn dispatch_action(action: &str, command_id: &str) {
    println!("⚡ [Action Dispatcher] Executando ação: {} (Comando: {})", action, command_id);
    
    if action.starts_with("open:") {
        let target = action.trim_start_matches("open:");
        if let Err(e) = open::that(target) {
            println!("❌ Erro ao abrir: {}", e);
        }
    } else if action.starts_with("sh:") {
        // NOVO: Execução de Shell Scripts e AppleScript nativos!
        let script = action.trim_start_matches("sh:").to_string();
        std::thread::spawn(move || {
            match std::process::Command::new("sh").arg("-c").arg(&script).output() {
                Ok(output) => {
                    if !output.status.success() {
                        let err = String::from_utf8_lossy(&output.stderr);
                        println!("❌ Erro no script: {}", err);
                    } else {
                        println!("✅ Script executado com sucesso!");
                    }
                }
                Err(e) => println!("❌ Falha ao invocar terminal: {}", e),
            }
        });
    } else {
        match action {
            "play_music" => {
                if let Err(e) = open::that("spotify:") {
                    println!("❌ Erro ao abrir Spotify: {}", e);
                }
            }
            "get_time" => {
                let now = chrono::Local::now();
                println!("🕰️ Agora são: {}", now.format("%H:%M"));
            }
            _ => println!("⚠️ Nenhuma lógica mapeada para a ação: {}", action),
        }
    }
}

#[tauri::command]
async fn send_to_pipeline(
    message: serde_json::Value,
    pipeline_state: tauri::State<'_, PipelineState>,
) -> Result<(), String> {
    // Extrai o tipo e o payload que vieram do React
    let msg_type = message.get("type").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let payload = message.get("payload").cloned().unwrap_or(serde_json::json!({}));

    // Cria a mensagem estruturada e envia para o canal do Python
    let pipeline_msg = PipelineMessage { msg_type, payload };
    pipeline_state.tx.send(pipeline_msg).await.map_err(|e| e.to_string())?;
    
    Ok(())
}

#[tauri::command]
fn start_recording(audio_state: State<'_, AudioState>) -> Result<(), String> {
    audio_state.is_recording.store(true, Ordering::SeqCst);
    Ok(())
}

#[tauri::command]
fn stop_recording(audio_state: State<'_, AudioState>) -> Result<(), String> {
    audio_state.is_recording.store(false, Ordering::SeqCst);
    Ok(())
}

#[tauri::command]
fn get_commands(state: State<'_, AppState>) -> Result<Vec<db::CommandEntry>, String> {
    db::get_commands(&state.db_path).map_err(|e| e.to_string())
}

#[tauri::command]
async fn add_command(
    state: State<'_, AppState>,
    pipeline: State<'_, PipelineState>,
    cmd: db::CommandEntry
) -> Result<(), String> {
    db::add_command(&state.db_path, cmd).map_err(|e| e.to_string())?;
    
    let msg = PipelineMessage {
        msg_type: "config_reloaded".to_string(),
        payload: serde_json::json!({ "db_path": state.db_path }),
    };
    let _ = pipeline.tx.send(msg).await;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            // ==========================================
            // NOVO: Oculta do Dock e cria o ícone no Tray
            // ==========================================
            #[cfg(target_os = "macos")]
            app.set_activation_policy(ActivationPolicy::Accessory);

            let tray = TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("Jarvis AI")
                .on_tray_icon_event(|tray, event| match event {
                    TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } => {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            if window.is_visible().unwrap_or(false) {
                                let _ = window.hide();
                            } else {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                    }
                    _ => {}
                })
                .build(app)?;
            // ==========================================

            let current_dir = env::current_dir().unwrap();
            let monorepo_root = current_dir.parent().unwrap().parent().unwrap().parent().unwrap();
            
            // Inicializa o TTS nativo do Mac
            let mut tts = Tts::default().expect("Falha ao carregar TTS do SO");

            // Procura e força o uso de uma voz em Português
            if let Ok(voices) = tts.voices() {
                if let Some(br_voice) = voices.iter().find(|v| {
                    let lang = v.language().to_lowercase();
                    let name = v.name().to_lowercase();

                    lang.starts_with("pt-br") && name.contains("enhanced")
                }) {
                    let _ = tts.set_voice(br_voice);
                    println!("🗣️ TTS configurado para a voz: {}", br_voice.name());
                } else {
                    println!("⚠️ Nenhuma voz em português encontrada. Usando o padrão.");
                }
            }

            app.manage(TtsState { engine: Mutex::new(tts) });
            
            let db_path = monorepo_root.join("vox.db").to_string_lossy().to_string();
            db::init_db(&db_path).expect("Falha ao criar o banco de dados");
            app.manage(AppState { db_path: db_path.clone() });
            
            let python_dir = monorepo_root.join("packages").join("vox-core-python");
            let app_handle = app.handle().clone();
            
            let (tx, mut rx) = mpsc::channel::<PipelineMessage>(32);
            app.manage(PipelineState { tx: tx.clone() });
            
            let is_recording = Arc::new(AtomicBool::new(false));
            app.manage(AudioState { is_recording: is_recording.clone() });
            
            let (audio_tx, mut audio_rx) = mpsc::channel(16000 * 5);
            audio::start_microphone(is_recording.clone(), audio_tx);

            let tx_audio = tx.clone();
            
            tauri::async_runtime::spawn(async move {
                let chunk_size = 4800; // 100ms de áudio por pacote
                let mut stream_buffer = Vec::with_capacity(chunk_size);
                
                let frame_size = 480; 
                let mut frame_buffer = Vec::with_capacity(frame_size);
                let mut is_speaking = false;
                let mut silence_frames = 0;
                
                let energy_threshold = 0.04; 
                let max_silence_frames = 100; // 1 segundo exato de silêncio

                while let Some(sample) = audio_rx.recv().await {
                    if !is_recording.load(Ordering::SeqCst) {
                        stream_buffer.clear();
                        frame_buffer.clear();
                        is_speaking = false;
                        silence_frames = 0;
                        continue;
                    }

                    stream_buffer.push(sample);
                    frame_buffer.push(sample);
                    
                    if frame_buffer.len() >= frame_size {
                        let sq_sum: f32 = frame_buffer.iter().map(|&s| s * s).sum();
                        let rms = (sq_sum / frame_buffer.len() as f32).sqrt();

                        if is_speaking {
                            if rms < energy_threshold {
                                silence_frames += 1;
                                if silence_frames >= max_silence_frames {
                                    is_speaking = false;
                                    silence_frames = 0;
                                    
                                    let msg = PipelineMessage {
                                        msg_type: "audio_silence".to_string(),
                                        payload: serde_json::json!({}),
                                    };
                                    let _ = tx_audio.send(msg).await;
                                }
                            } else {
                                silence_frames = 0;
                            }
                        } else {
                            if rms >= energy_threshold {
                                is_speaking = true;
                                silence_frames = 0;
                            }
                        }
                        frame_buffer.clear();
                    }

                    if stream_buffer.len() >= chunk_size {
                        let bytes: Vec<u8> = stream_buffer.iter().flat_map(|&f| f.to_le_bytes()).collect();
                        use base64::{engine::general_purpose, Engine as _};
                        let b64 = general_purpose::STANDARD.encode(&bytes);

                        let msg = PipelineMessage {
                            msg_type: "audio_stream".to_string(),
                            payload: serde_json::json!({ "audio_b64": b64 }),
                        };
                        let _ = tx_audio.send(msg).await;
                        stream_buffer.clear();
                    }
                }
            });

            tauri::async_runtime::spawn(async move {
                let mut command = Command::new("uv");
                command.arg("run").arg("vox-pipeline").current_dir(python_dir).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::inherit()); 

                match command.spawn() {
                    Ok(mut child) => {
                        let mut stdin = child.stdin.take().expect("Falha ao capturar stdin");
                        let stdout = child.stdout.take().expect("Falha ao capturar stdout");

                        let init_msg = PipelineMessage {
                            msg_type: "config_reloaded".to_string(),
                            payload: serde_json::json!({ "db_path": db_path }),
                        };
                        let init_json = serde_json::to_string(&init_msg).unwrap() + "\n";
                        stdin.write_all(init_json.as_bytes()).await.unwrap();

                        let app_handle_clone = app_handle.clone();
                        tokio::spawn(async move {
                            let mut reader = BufReader::new(stdout).lines();
                            while let Ok(Some(line)) = reader.next_line().await {
                                if let Ok(msg) = serde_json::from_str::<PipelineMessage>(&line) {
                                    
                                    if msg.msg_type != "audio_stream" && msg.msg_type != "audio_silence" {
                                        let _ = app_handle_clone.emit("pipeline-response", msg.clone());
                                    }
                                    
                                    // Intercepta e muda a UI quando o Jarvis acordar/dormir
                                    if msg.msg_type == "wakeword_status" {
                                        if let Some(status) = msg.payload.get("status").and_then(|v| v.as_str()) {
                                            let _ = app_handle_clone.emit("wakeword-status", status);
                                            
                                            if status == "listening" {
                                                // NOVO: Cala a boca do Jarvis imediatamente!
                                                let state = app_handle_clone.state::<TtsState>();
                                                if let Ok(mut tts_engine) = state.engine.lock() {
                                                    let _ = tts_engine.stop();
                                                }; // <-- O famoso ponto e vírgula salvador!
                                                
                                                // Toca o som de alerta que ele está ouvindo
                                                play_mac_sound("Tink");
                                            }
                                        }
                                    }

                                    // ==========================================
                                    // MÁGICA: Quando o LLM responde, o Rust fala!
                                    // ==========================================
                                    if msg.msg_type == "llm_response" {
                                        if let Some(resp) = msg.payload.get("response").and_then(|v| v.as_str()) {
                                            println!("🤖 Jarvis (LLM): {}", resp);
                                            let _ = app_handle_clone.emit("llm-response", resp);
                                            
                                            let state = app_handle_clone.state::<TtsState>();
                                            if let Ok(mut tts_engine) = state.engine.lock() {
                                                // O `true` significa que ele interrompe a si mesmo se estiver falando outra coisa
                                                let _ = tts_engine.speak(resp, true);
                                            };
                                        }
                                    }
                                    
                                    if msg.msg_type == "intent_match" {
                                        if let Some(intent) = msg.payload.get("intent") {
                                            let method = intent.get("method").and_then(|v| v.as_str()).unwrap_or("unmatched");
                                            
                                            if method != "unmatched" {
                                                let action = intent.get("action").and_then(|v| v.as_str()).unwrap_or("");
                                                let command_id = intent.get("command_id").and_then(|v| v.as_str()).unwrap_or("");
                                                
                                                // NOVO: Feedback sonoro de sucesso!
                                                play_mac_sound("Pop");

                                                dispatch_action(action, command_id);
                                            } else {
                                                let phrase = intent.get("matched_phrase").and_then(|v| v.as_str()).unwrap_or("");
                                                println!("🤔 Intenção não reconhecida (Ignorado): '{}'", phrase);
                                            }
                                        }
                                    }
                                }
                            }
                        });

                        tokio::spawn(async move {
                            while let Some(msg) = rx.recv().await {
                                if let Ok(mut json) = serde_json::to_string(&msg) {
                                    json.push('\n');
                                    if stdin.write_all(json.as_bytes()).await.is_err() { break; }
                                }
                            }
                        });
                        
                        let _ = child.wait().await;
                    }
                    Err(e) => println!("❌ Falha crítica: Erro: {}", e),
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            send_to_pipeline,
            start_recording, 
            stop_recording,
            get_commands,
            add_command
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
