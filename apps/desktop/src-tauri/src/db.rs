use rusqlite::{Connection, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct CommandEntry {
    pub id: String,
    pub action: String,
    pub phrases: String,
}

pub fn init_db(db_path: &str) -> Result<()> {
    let conn = Connection::open(db_path)?;
    
    conn.execute(
        "CREATE TABLE IF NOT EXISTS commands (
            id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            phrases TEXT NOT NULL
        )",
        (),
    )?;
    
    let count: i64 = conn.query_row("SELECT COUNT(*) FROM commands", (), |row| row.get(0))?;
    if count == 0 {
        conn.execute("INSERT INTO commands (id, action, phrases) VALUES ('spotify_play', 'play_music', 'toca música,tocar música,play,solta o som')", ())?;
        conn.execute("INSERT INTO commands (id, action, phrases) VALUES ('browser_open', 'open:https://google.com', 'abrir chrome,abre o navegador')", ())?;
        conn.execute("INSERT INTO commands (id, action, phrases) VALUES ('system_time', 'get_time', 'que horas são,me diga as horas')", ())?;
    }
    Ok(())
}

pub fn get_commands(db_path: &str) -> Result<Vec<CommandEntry>> {
    let conn = Connection::open(db_path)?;
    let mut stmt = conn.prepare("SELECT id, action, phrases FROM commands ORDER BY id")?;
    
    let iter = stmt.query_map((), |row| {
        Ok(CommandEntry {
            id: row.get(0)?,
            action: row.get(1)?,
            phrases: row.get(2)?,
        })
    })?;
    
    let mut commands = Vec::new();
    for cmd in iter {
        commands.push(cmd?);
    }
    Ok(commands)
}

pub fn add_command(db_path: &str, cmd: CommandEntry) -> Result<()> {
    let conn = Connection::open(db_path)?;
    conn.execute(
        "INSERT OR REPLACE INTO commands (id, action, phrases) VALUES (?1, ?2, ?3)",
        (&cmd.id, &cmd.action, &cmd.phrases),
    )?;
    Ok(())
}
