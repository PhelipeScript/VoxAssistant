use serde::{Deserialize, Serialize};
use std::process::Stdio;
use tauri::{Emitter, Manager, State};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;
use tokio::sync::mpsc;
use std::env;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

mod audio;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct PipelineMessage {
    #[serde(rename = "type")]
    pub msg_type: String,
    pub payload: serde_json::Value,
}

pub struct PipelineState {
    tx: mpsc::Sender<PipelineMessage>,
}

pub struct AudioState {
    pub is_recording: Arc<AtomicBool>,
}

#[tauri::command]
async fn send_to_pipeline(
    state: State<'_, PipelineState>,
    message: PipelineMessage,
) -> Result<(), String> {
    state.tx.send(message).await.map_err(|e| e.to_string())
}

#[tauri::command]
fn start_recording(audio_state: State<'_, AudioState>) -> Result<(), String> {
    audio_state.is_recording.store(true, Ordering::SeqCst);
    println!("🔴 Gravação Iniciada (Aguardando você falar...)");
    Ok(())
}

#[tauri::command]
fn stop_recording(audio_state: State<'_, AudioState>) -> Result<(), String> {
    audio_state.is_recording.store(false, Ordering::SeqCst);
    println!("⏹️ Gravação Parada!");
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let current_dir = env::current_dir().unwrap();
            
            let python_dir = current_dir
                .parent().unwrap()
                .parent().unwrap()
                .parent().unwrap()
                .join("packages")
                .join("vox-core-python");

            let app_handle = app.handle().clone();
            
            let (tx, mut rx) = mpsc::channel::<PipelineMessage>(32);
            app.manage(PipelineState { tx: tx.clone() });
            
            let is_recording = Arc::new(AtomicBool::new(false));
            app.manage(AudioState { is_recording: is_recording.clone() });
            
            let (audio_tx, mut audio_rx) = mpsc::channel::<f32>(16000 * 5); 
            
            audio::start_microphone(is_recording.clone(), audio_tx);

            // A MÁGICA ACONTECE AQUI: Thread de VAD (Voice Activity Detection)
            let tx_audio = tx.clone();
            let app_handle_clone = app_handle.clone();
            
            tauri::async_runtime::spawn(async move {
                // Para 48kHz, 10 milissegundos de áudio = 480 amostras
                let frame_size = 480; 
                let mut frame_buffer = Vec::with_capacity(frame_size);
                let mut speech_buffer = Vec::new();
                
                let mut is_speaking = false;
                let mut silence_frames = 0;
                
                // Variáveis de Calibragem do VAD (Pode ajustar se ficar muito sensível)
                let energy_threshold = 0.015; // O volume mínimo para considerar "voz"
                let max_silence_frames = 100; // 1 segundo de silêncio (100 frames x 10ms) antes de cortar

                while let Some(sample) = audio_rx.recv().await {
                    // Se o usuário clicou em Parar, limpamos o buffer e resetamos o estado
                    if !is_recording.load(Ordering::SeqCst) {
                        frame_buffer.clear();
                        speech_buffer.clear();
                        is_speaking = false;
                        silence_frames = 0;
                        continue;
                    }

                    frame_buffer.push(sample);
                    
                    // Quando acumulamos 10ms, analisamos
                    if frame_buffer.len() >= frame_size {
                        // Calcula RMS (Energia) do frame
                        let sq_sum: f32 = frame_buffer.iter().map(|&s| s * s).sum();
                        let rms = (sq_sum / frame_buffer.len() as f32).sqrt();

                        if is_speaking {
                            speech_buffer.extend_from_slice(&frame_buffer);
                            
                            // Se ficou silencioso
                            if rms < energy_threshold {
                                silence_frames += 1;
                                
                                // Se o silêncio durou mais que o limite, a frase acabou!
                                if silence_frames >= max_silence_frames {
                                    // Só envia se tivermos pelo menos 0.5 segundos de áudio (ignora tosses rápidas)
                                    if speech_buffer.len() > 24000 {
                                        println!("🗣️ VAD: Frase concluída. Enviando áudio dinâmico...");
                                        let bytes: Vec<u8> = speech_buffer.iter()
                                            .flat_map(|&f| f.to_le_bytes())
                                            .collect();

                                        use base64::{engine::general_purpose, Engine as _};
                                        let b64 = general_purpose::STANDARD.encode(&bytes);

                                        let msg = PipelineMessage {
                                            msg_type: "audio_chunk".to_string(),
                                            payload: serde_json::json!({
                                                "audio_b64": b64,
                                                "sample_rate": 48000,
                                                "channels": 1
                                            }),
                                        };

                                        if tx_audio.send(msg).await.is_err() { break; }
                                    } else {
                                        println!("🔇 VAD: Áudio muito curto, ignorado.");
                                    }
                                    
                                    // Reseta a máquina de estados para a próxima frase
                                    speech_buffer.clear();
                                    is_speaking = false;
                                    silence_frames = 0;
                                }
                            } else {
                                // A pessoa voltou a falar antes de dar 1 segundo, zera a contagem de silêncio
                                silence_frames = 0;
                            }
                        } else {
                            // Se não estava falando, verifica se o ruído bateu na trave
                            if rms >= energy_threshold {
                                println!("🗣️ VAD: Voz detectada! Gravando frase...");
                                is_speaking = true;
                                silence_frames = 0;
                                speech_buffer.extend_from_slice(&frame_buffer);
                            }
                        }
                        
                        // Limpa o frame atual para pegar os próximos 10ms
                        frame_buffer.clear();
                    }
                }
            });

            tauri::async_runtime::spawn(async move {
                let mut command = Command::new("uv");
                command
                    .arg("run")
                    .arg("vox-pipeline")
                    .current_dir(python_dir)
                    .stdin(Stdio::piped())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::inherit()); 

                match command.spawn() {
                    Ok(mut child) => {
                        let mut stdin = child.stdin.take().expect("Falha ao stdin");
                        let stdout = child.stdout.take().expect("Falha ao stdout");

                        let app_handle_clone = app_handle.clone();
                        tokio::spawn(async move {
                            let mut reader = BufReader::new(stdout).lines();
                            while let Ok(Some(line)) = reader.next_line().await {
                                if let Ok(msg) = serde_json::from_str::<PipelineMessage>(&line) {
                                    
                                    let _ = app_handle_clone.emit("pipeline-response", msg.clone());
                                    
                                    if msg.msg_type == "intent_match" {
                                        if let Some(intent) = msg.payload.get("intent") {
                                            if let Some(command_id) = intent.get("command_id").and_then(|v| v.as_str()) {
                                                dispatch_action(command_id);
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
            stop_recording
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn dispatch_action(command_id: &str) {
    println!("⚡ [Action Dispatcher] Executando comando: {}", command_id);
    
    match command_id {
        "spotify_play" => {
            println!("🎵 Abrindo o Spotify...");
            // No Mac, abrir o scheme "spotify:" acorda o app nativo instantaneamente
            if let Err(e) = open::that("spotify:") {
                println!("❌ Erro ao abrir Spotify: {}", e);
            }
        }
        "browser_open" => {
            println!("🌐 Abrindo o Navegador...");
            if let Err(e) = open::that("https://google.com") {
                println!("❌ Erro ao abrir Navegador: {}", e);
            }
        }
        "system_time" => {
            let now = chrono::Local::now();
            println!("🕰️ O usuário pediu as horas! Agora são: {}", now.format("%H:%M"));
            // Na Fase 2, aqui nós acionaríamos o TTS (Text-to-Speech) para o Vox "falar" a hora.
        }
        _ => {
            println!("⚠️ Nenhuma ação mapeada para o comando: {}", command_id);
        }
    }
}
