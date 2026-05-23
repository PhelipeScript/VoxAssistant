use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tokio::sync::mpsc;

pub const TARGET_SAMPLE_RATE: u32 = 16000;

pub fn start_microphone(
    is_recording: Arc<AtomicBool>,
    tx: mpsc::Sender<f32>,
) {
    // 1. Criamos uma thread nativa do SO. O stream de áudio vai viver e morrer aqui.
    std::thread::spawn(move || {
        let host = cpal::default_host();
        let device = match host.default_input_device() {
            Some(d) => d,
            None => {
                eprintln!("❌ Nenhum dispositivo de entrada de áudio encontrado");
                return;
            }
        };

        // Silenciaremos o aviso de depreciação do cpal usando unwrap_or_else
        println!("🎙️ Usando microfone: {}", device.name().unwrap_or_else(|_| "Desconhecido".to_string()));

        let config = device.default_input_config().unwrap();
        let channels = config.channels();
        
        println!("⚙️ Config do Mic: {} Hz, {} canais, Formato: {:?}", config.sample_rate().0, channels, config.sample_format());

        let err_fn = |err| eprintln!("❌ Erro no stream de áudio: {}", err);

        // 2. O Callback de áudio. É disparado a cada ~10ms pelo SO.
        let stream = match config.sample_format() {
            cpal::SampleFormat::F32 => device.build_input_stream(
                &config.into(),
                move |data: &[f32], _: &cpal::InputCallbackInfo| {
                    // Verificação atômica super rápida e segura
                    if is_recording.load(Ordering::SeqCst) {
                        for frame in data.chunks(channels as usize) {
                            // try_send é Lock-Free, perfeito para callbacks de áudio de alta performance
                            let _ = tx.try_send(frame[0]); 
                        }
                    }
                },
                err_fn,
                None,
            ).unwrap(),
            _ => {
                eprintln!("❌ Formato de áudio não suportado nativamente pelo App.");
                return;
            }
        };

        stream.play().unwrap();
        
        // 3. Estaciona a thread para sempre. Isso impede que a variável 'stream' 
        // seja limpa da memória, mantendo o microfone ligado em background.
        loop {
            std::thread::park();
        }
    });
}
