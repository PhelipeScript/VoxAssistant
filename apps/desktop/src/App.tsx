import { useState, useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

// Interfaces do Tauri
interface PipelineMessage {
  type: string;
  payload: any;
}
interface CommandEntry {
  id: string;
  action: string;
  phrases: string;
}
type ChatMessage = { id: number; role: "user" | "jarvis"; text: string };
type AIState = "idle" | "listening" | "thinking" | "speaking" | "error";

export default function App() {
  const [activeTab, setActiveTab] = useState<"chat" | "settings">("chat");
  const [aiState, setAiState] = useState<AIState>("idle");
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState("");
  
  const [commands, setCommands] = useState<CommandEntry[]>([]);
  const [newCmd, setNewCmd] = useState<CommandEntry>({ id: "", action: "", phrases: "" });
  const chatEndRef = useRef<HTMLDivElement>(null);

  const [thinkingText, setThinkingText] = useState("Pensando...");
  const thinkingTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // NOVO: Função para deletar comando
  const handleDeleteCommand = async (id: string) => {
    await invoke("delete_command", { id });
    // Recarrega a lista do banco
    setCommands(await invoke("get_commands")); 
  };

  // Auto-scroll do chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat]);

  // Carrega comandos do SQLite no boot
  useEffect(() => {
    invoke<CommandEntry[]>("get_commands").then(setCommands).catch(console.error);
  }, []);

  // Escuta os eventos do Backend (Rust + Python)
  useEffect(() => {
    const unlisten = listen<PipelineMessage>("pipeline-response", (event) => {
      const msg = event.payload;

      if (msg.type === "llm_response") {
        setAiState("speaking"); // O TTS começou a falar
        setChat((prev) => [
          ...prev,
          { id: Date.now(), role: "user", text: msg.payload.transcript },
          { id: Date.now() + 1, role: "jarvis", text: msg.payload.response }
        ]);
        // Simula a volta para o repouso após 4 segundos (já que o TTS não nos avisa quando termina)
        setTimeout(() => setAiState("idle"), 4000);
      } 
      else if (msg.type === "intent_match") {
        if (msg.payload.intent.method !== "unmatched") {
          setAiState("idle"); // Ação rápida executada
          setChat((prev) => [
            ...prev,
            { id: Date.now(), role: "user", text: msg.payload.transcript },
            { id: Date.now() + 1, role: "jarvis", text: `⚡ Executando: ${msg.payload.intent.action}` }
          ]);
        }
      }
    });

    const unlistenWake = listen<string>("wakeword-status", (event) => {
      if (event.payload === "listening") {
        setAiState("listening");
        if (thinkingTimeoutRef.current) clearTimeout(thinkingTimeoutRef.current);
      } else if (event.payload === "processing") {
        setAiState("thinking");
        setThinkingText("Analisando...");
        
        // Se demorar mais de 3 segundos, muda o texto para acalmar o usuário
        thinkingTimeoutRef.current = setTimeout(() => {
          setThinkingText("Processando contexto, só um instante...");
        }, 3000);

      } else {
        setAiState("idle");
        if (thinkingTimeoutRef.current) clearTimeout(thinkingTimeoutRef.current);
      }
    });

    return () => {
      unlisten.then((f) => f());
      unlistenWake.then((f) => f());
    };
  }, []);

  const handleAddCommand = async () => {
    if (!newCmd.id || !newCmd.action || !newCmd.phrases) return;
    await invoke("add_command", { cmd: newCmd });
    setCommands(await invoke("get_commands"));
    setNewCmd({ id: "", action: "", phrases: "" });
  };

  const handleSendText = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    
    // Adiciona ao chat localmente
    setChat((prev) => [...prev, { id: Date.now(), role: "user", text: inputText }]);
    
    // Envia para o Pipeline do Rust/Python
    await invoke("send_to_pipeline", { 
      message: { type: "text_input", payload: { text: inputText } } 
    });
    
    setInputText("");
    setAiState("thinking"); // Mostra que está processando o texto
  };

  // ==========================================
  // COMPONENTES VISUAIS INTERNOS
  // ==========================================

  const [isMicActive, setIsMicActive] = useState(false);

  const toggleMic = async () => {
    if (!isMicActive)
      await invoke("start_recording"); 
    else
      await invoke("stop_recording");
    setIsMicActive(!isMicActive);
  };

  // O Orbe de Status do Jarvis
  const OrbVisualizer = () => {
    const states = {
      idle: "bg-slate-600 shadow-[0_0_15px_rgba(71,85,105,0.3)] scale-100",
      listening: "bg-emerald-500 shadow-[0_0_30px_rgba(16,185,129,0.8)] animate-pulse scale-110",
      thinking: "bg-purple-500 shadow-[0_0_30px_rgba(168,85,247,0.8)] animate-spin border-dashed border-4 border-purple-300 scale-100",
      speaking: "bg-cyan-500 shadow-[0_0_40px_rgba(6,182,212,0.9)] animate-bounce scale-105",
      error: "bg-red-500 shadow-[0_0_30px_rgba(239,68,68,0.8)] scale-100",
    };

    return (
      <div className="flex flex-col items-center justify-center py-6">
        <div className={`w-16 h-16 rounded-full transition-all duration-300 ${states[aiState]}`} />
        <span className="text-xs text-slate-400 mt-3 font-medium uppercase tracking-widest">
          {aiState === "thinking" ? thinkingText : aiState}
        </span>
      </div>
    );
  };

  return (
    <div className="h-screen flex flex-col bg-slate-950 text-slate-200 overflow-hidden font-sans">
      
      {/* 1. Barra de Arrastar (Para mover a janela frameless) */}
      <div data-tauri-drag-region className="h-8 w-full bg-slate-900/50 flex items-center justify-center cursor-grab active:cursor-grabbing border-b border-slate-800">
        <div data-tauri-drag-region className="w-12 h-1 bg-slate-700 rounded-full" />
      </div>

      {/* 2. O Visualizador Central */}
      <OrbVisualizer />

      <button 
        onClick={toggleMic} 
        className={`absolute top-10 right-4 text-xs px-3 py-1 rounded-full border ${isMicActive ? 'bg-red-500/20 border-red-500 text-red-400' : 'bg-slate-800 border-slate-700 text-slate-400'}`}
      >
        {isMicActive ? "🎙️ Mic ON" : "🔇 Mic OFF"}
      </button>

      {/* 3. Área Principal (Abas) */}
      <div className="flex-1 overflow-hidden relative">
        
        {/* ABA DE CHAT */}
        {activeTab === "chat" && (
          <div className="absolute inset-0 flex flex-col p-4">
            <div className="flex-1 overflow-y-auto space-y-4 pr-2 custom-scrollbar">
              {chat.length === 0 ? (
                <div className="h-full flex items-center justify-center text-slate-600 text-sm">
                  Diga "Hey Jarvis" ou digite um comando...
                </div>
              ) : (
                chat.map((msg) => (
                  <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                    <div className={`max-w-[85%] p-3 text-sm leading-relaxed rounded-2xl shadow-md ${
                      msg.role === "user" 
                        ? "bg-emerald-600 text-emerald-50 rounded-br-none" 
                        : "bg-slate-800 border border-slate-700 text-blue-100 rounded-bl-none"
                    }`}>
                      {msg.role === "jarvis" && <span className="text-xs text-blue-400 font-bold block mb-1">Jarvis</span>}
                      {msg.text}
                    </div>
                  </div>
                ))
              )}
              <div ref={chatEndRef} />
            </div>

            {/* Input de Texto Híbrido */}
            <form onSubmit={handleSendText} className="mt-4 flex gap-2">
              <input
                type="text"
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder="Escreva para o Jarvis..."
                className="flex-1 bg-slate-900 border border-slate-700 rounded-full px-4 py-2 text-sm focus:outline-none focus:border-emerald-500 transition-colors"
              />
              <button type="submit" className="bg-emerald-600 hover:bg-emerald-500 text-white p-2 w-10 h-10 rounded-full flex items-center justify-center shadow-lg transition-colors">
                ➤
              </button>
            </form>
          </div>
        )}

        {/* ABA DE CONFIGURAÇÕES (Comandos) */}
        {activeTab === "settings" && (
          <div className="absolute inset-0 p-4 overflow-y-auto custom-scrollbar">
            <h2 className="text-lg font-bold mb-4 text-emerald-400">Árvore de Intenções</h2>
            
            <div className="bg-slate-900 p-4 rounded-xl border border-slate-800 mb-6 shadow-inner">
              <div className="space-y-3">
                <input placeholder="ID (ex: abrir_youtube)" value={newCmd.id} onChange={(e) => setNewCmd({...newCmd, id: e.target.value})} className="w-full bg-slate-950 border border-slate-800 p-2 rounded text-sm text-slate-300 focus:border-emerald-500 outline-none" />
                <input placeholder="Ação (ex: open:https://youtube.com)" value={newCmd.action} onChange={(e) => setNewCmd({...newCmd, action: e.target.value})} className="w-full bg-slate-950 border border-slate-800 p-2 rounded text-sm text-slate-300 focus:border-emerald-500 outline-none" />
                <textarea placeholder="Frases (separadas por vírgula)" value={newCmd.phrases} onChange={(e) => setNewCmd({...newCmd, phrases: e.target.value})} className="w-full bg-slate-950 border border-slate-800 p-2 rounded text-sm text-slate-300 focus:border-emerald-500 outline-none min-h-[80px]" />
                <button onClick={handleAddCommand} className="w-full bg-slate-800 hover:bg-slate-700 text-emerald-400 font-medium py-2 rounded transition-colors border border-slate-700">
                  + Adicionar Regra
                </button>
              </div>
            </div>

            <div className="space-y-3 pb-8">
              {commands.map((c) => (
                <div key={c.id} className="bg-slate-800/50 p-3 rounded-lg border border-slate-700/50">
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-emerald-400 font-mono text-xs">{c.id}</span>
                    <button 
                      onClick={() => handleDeleteCommand(c.id)}
                      className="text-red-500 hover:text-red-400 text-xs p-1"
                      title="Deletar Comando"
                    >
                      🗑️
                    </button>
                  </div>
                  <div className="text-xs text-slate-400 break-all">{c.action}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 4. Barra de Navegação Inferior */}
      <div className="h-14 bg-slate-900 border-t border-slate-800 flex justify-around items-center px-4">
        <button 
          onClick={() => setActiveTab("chat")}
          className={`flex-1 py-2 text-sm font-medium transition-colors ${activeTab === "chat" ? "text-emerald-400" : "text-slate-500 hover:text-slate-300"}`}
        >
          💬 Chat
        </button>
        <button 
          onClick={() => setActiveTab("settings")}
          className={`flex-1 py-2 text-sm font-medium transition-colors ${activeTab === "settings" ? "text-emerald-400" : "text-slate-500 hover:text-slate-300"}`}
        >
          ⚙️ Comandos
        </button>
      </div>

    </div>
  );
}
