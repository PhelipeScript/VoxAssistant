import { useState, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

interface PipelineMessage {
  type: string;
  payload: Record<string, any>;
}

interface CommandEntry {
  id: string;
  action: string;
  phrases: string;
}

function App() {
  const [pipelineStatus, setPipelineStatus] = useState<string>("Desconectado");
  const [lastResponse, setLastResponse] = useState<PipelineMessage | null>(null);
  
  const [isRecording, setIsRecording] = useState(false); // Hardware do mic ligado/desligado
  const [jarvisAwake, setJarvisAwake] = useState(false); // IA prestando atenção no comando
  
  const [llmMessage, setLlmMessage] = useState<string>("");
  const [commands, setCommands] = useState<CommandEntry[]>([]);
  const [newCmd, setNewCmd] = useState<CommandEntry>({ id: "", action: "", phrases: "" });

  useEffect(() => {
    // Escuta as respostas do Python
    const unlisten = listen<PipelineMessage>("pipeline-response", (event) => {
      setLastResponse(event.payload);
      if (event.payload.type === "pong") {
        setPipelineStatus("Conectado (Ping Ok)");
      }
    });

    // Escuta o status do Jarvis
    const unlistenWakeWord = listen("wakeword-status", (event) => {
      if (event.payload === "listening") {
        setJarvisAwake(true);
      } else if (event.payload === "idle") {
        setJarvisAwake(false);
      }
    });

    // Escuta respostas do LLM (Ollama)
    const unlistenLlm = listen<string>("llm-response", (event) => {
      setLlmMessage(event.payload);
    });

    invoke("send_to_pipeline", { message: { type: "ping", payload: {} } });
    fetchCommands();

    return () => {
      unlisten.then((fn) => fn());
      unlistenWakeWord.then((fn) => fn());
      unlistenLlm.then((fn) => fn());
    };
  }, []);

  const fetchCommands = async () => {
    try {
      const cmds = await invoke<CommandEntry[]>("get_commands");
      setCommands(cmds);
    } catch (error) {
      console.error("Erro ao buscar comandos:", error);
    }
  };

  const handleAddCommand = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newCmd.id || !newCmd.action || !newCmd.phrases) return;

    try {
      // O Rust salva no SQLite e avisa o Python automaticamente!
      await invoke("add_command", { cmd: newCmd });
      setNewCmd({ id: "", action: "", phrases: "" }); // Limpa o formulário
      fetchCommands(); // Atualiza a lista
    } catch (error) {
      console.error("Erro ao salvar comando:", error);
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
    <main className="container mx-auto p-8 font-sans bg-white min-h-screen text-slate-800">
      <h1 className="text-4xl font-bold mb-8 text-slate-800">Vox Assistant</h1>
      
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        
        {/* COLUNA ESQUERDA: Controles e Logs */}
        <div className="flex flex-col gap-6">
          <div className="bg-slate-50 p-6 rounded-xl border border-slate-200 shadow-sm">
            <h2 className="text-xl font-bold mb-4 flex items-center gap-2">
              <span className={`w-3 h-3 rounded-full ${pipelineStatus.includes('Conectado') ? 'bg-emerald-500' : 'bg-red-500'}`}></span>
              Core & Microfone
            </h2>
            
            <button 
              onClick={toggleRecording}
              className={`w-full font-bold py-4 rounded-lg transition-all shadow-sm flex items-center justify-center gap-3 text-lg ${
                isRecording 
                ? 'bg-red-500 hover:bg-red-600 text-white' 
                : 'bg-emerald-600 hover:bg-emerald-700 text-white'
              }`}
            >
              {jarvisAwake ? (
                'Jarvis Ouvindo Comando...'
              ) : isRecording ? (
                <>
                  <span className="w-3 h-3 bg-white rounded-full animate-pulse"></span>
                  Microfone Ativo (Diga "Hey Jarvis")
                </>
              ) : (
                'Ativar Microfone'
              )}
            </button>
            <p className="text-sm text-slate-500 mt-3 text-center">
              O VAD enviará o áudio automaticamente após 1s de silêncio.
            </p>
          </div>

          <div className="bg-slate-900 text-emerald-400 p-4 rounded-xl font-mono text-sm overflow-auto h-72 shadow-inner border border-slate-800">
            <p className="text-slate-400 mb-2 border-b border-slate-700 pb-2">// Live Pipeline Log</p>
            <pre className="whitespace-pre-wrap break-words">
              {/* {lastResponse ? JSON.stringify(lastResponse, null, 2) : "Aguardando..."} */}
              {llmMessage && (
                <div className="text-emerald-400">
                  <strong>🤖 Jarvis (LLM):</strong> {llmMessage}
                </div>
              )}
              {lastResponse && lastResponse?.type != 'llm_response' && (
                <div className="mt-2">
                  <strong className="text-slate-400">📡 Pipeline:</strong> {lastResponse.type} - {JSON.stringify(lastResponse.payload)}
                </div>
              )}
            </pre>
          </div>
        </div>

        {/* COLUNA DIREITA: Gerenciador de Comandos */}
        <div className="bg-slate-50 p-6 rounded-xl border border-slate-200 shadow-sm flex flex-col h-full">
          <h2 className="text-xl font-bold mb-4">Registry (Comandos)</h2>
          
          <form onSubmit={handleAddCommand} className="mb-6 bg-white p-4 rounded-lg border border-slate-200 shadow-sm">
            <h3 className="text-sm font-bold text-slate-500 uppercase tracking-wider mb-3">Novo Comando</h3>
            <div className="grid grid-cols-2 gap-3 mb-3">
              <input
                className="border p-2 rounded bg-slate-50 focus:bg-white outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="ID (ex: abrir_youtube)"
                value={newCmd.id}
                onChange={(e) => setNewCmd({...newCmd, id: e.target.value})}
              />
              <input
                className="border p-2 rounded bg-slate-50 focus:bg-white outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Ação (ex: open:https://youtube.com)"
                value={newCmd.action}
                onChange={(e) => setNewCmd({...newCmd, action: e.target.value})}
              />
            </div>
            <input
              className="w-full border p-2 rounded bg-slate-50 focus:bg-white outline-none focus:ring-2 focus:ring-blue-500 mb-3"
              placeholder="Frases separadas por vírgula (ex: abrir youtube, abre o youtube)"
              value={newCmd.phrases}
              onChange={(e) => setNewCmd({...newCmd, phrases: e.target.value})}
            />
            <button type="submit" className="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 rounded transition-colors">
              Salvar Comando
            </button>
          </form>

          <div className="flex-1 overflow-auto bg-white rounded-lg border border-slate-200">
            <table className="w-full text-sm text-left">
              <thead className="bg-slate-100 text-slate-600 font-bold sticky top-0">
                <tr>
                  <th className="p-3">ID</th>
                  <th className="p-3">Ação</th>
                  <th className="p-3">Frases de Gatilho</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {commands.map((cmd) => (
                  <tr key={cmd.id} className="hover:bg-slate-50">
                    <td className="p-3 font-mono text-xs text-blue-600">{cmd.id}</td>
                    <td className="p-3 font-mono text-xs text-emerald-600">{cmd.action}</td>
                    <td className="p-3 text-slate-600 italic">{cmd.phrases}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

      </div>
    </main>
  );
}

export default App;
