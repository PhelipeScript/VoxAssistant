import { useState, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import "./App.css";

interface PipelineMessage {
  type: string;
  payload: Record<string, any>;
}

function App() {
  const [pipelineStatus, setPipelineStatus] = useState<string>("Desconectado");
  const [lastResponse, setLastResponse] = useState<PipelineMessage | null>(null);
  const [isRecording, setIsRecording] = useState(false);

  useEffect(() => {
    const unlisten = listen<PipelineMessage>("pipeline-response", (event) => {
      console.log("Mensagem recebida do Python via Rust:", event.payload);
      setLastResponse(event.payload);
      
      if (event.payload.type === "pong") {
        setPipelineStatus("Conectado (Ping Ok)");
      }
    });

    invoke("send_to_pipeline", {
      message: { type: "ping", payload: {} },
    });

    return () => {
      unlisten.then((fn) => fn());
    };
  }, []);

  const handleTestAudioChunk = async () => {
    try {
      await invoke("send_to_pipeline", {
        message: {
          type: "audio_chunk",
          payload: { placeholder: true },
        },
      });
    } catch (error) {
      console.error("Erro ao enviar comando IPC:", error);
    }
  };

  const toggleRecording = async () => {
    try {
      if (isRecording) {
        await invoke("stop_recording");
        setIsRecording(false);
      } else {
        await invoke("start_recording");
        setIsRecording(true);
      }
    } catch (error) {
      console.error("Erro ao alternar gravação:", error);
    }
  };

  return (
    <main className="container mx-auto p-8 font-sans">
      <h1 className="text-4xl font-bold mb-4 text-slate-800">Vox Assistant</h1>
      
      <div className="bg-slate-100 p-4 rounded-lg mb-6 shadow-sm">
        <h2 className="text-lg font-semibold mb-2">Controles do Core</h2>
        
        <div className="flex items-center gap-2 mb-4">
          <div className={`w-3 h-3 rounded-full ${pipelineStatus.includes('Conectado') ? 'bg-green-500' : 'bg-red-500'}`}></div>
          <span className="text-slate-700 font-mono text-sm">{pipelineStatus}</span>
        </div>
        
        <div className="flex gap-4">
          <button 
            onClick={toggleRecording}
            className={`font-medium py-2 px-6 rounded transition-colors shadow-sm flex items-center gap-2 ${
              isRecording 
              ? 'bg-red-600 hover:bg-red-700 text-white' 
              : 'bg-emerald-600 hover:bg-emerald-700 text-white'
            }`}
          >
            {isRecording ? (
              <>
                <span className="w-2 h-2 bg-white rounded-full animate-pulse"></span>
                Parar Captura do Microfone
              </>
            ) : (
              'Testar Microfone Real'
            )}
          </button>

          <button 
            onClick={handleTestAudioChunk}
            className="bg-slate-300 hover:bg-slate-400 text-slate-800 font-medium py-2 px-4 rounded transition-colors shadow-sm"
          >
            Simular Whisper ("audio_chunk")
          </button>
        </div>
      </div>

      <div className="bg-slate-800 text-emerald-400 p-4 rounded-lg font-mono text-sm overflow-auto h-64 shadow-inner">
        <p className="text-slate-400 mb-2">// Última resposta do subprocesso Python</p>
        <pre>
          {lastResponse ? JSON.stringify(lastResponse, null, 2) : "Aguardando..."}
        </pre>
      </div>
    </main>
  );
}

export default App;
