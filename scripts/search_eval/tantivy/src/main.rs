//! ulis-tantivy: EXPERIMENTAL Tantivy candidate stage for ULIS search (evaluation only).
//!
//! Subcommands:
//!   build --index DIR --mode pg|native [--heap-mb 1000] [--threads 2] [--commit-every 100000] [--merge] [--progress]
//!       JSON rows on stdin (ascending passage id); resumable: each commit stores the last id.
//!   merge --index DIR          merge all segments into one
//!   info --index DIR           JSON: docs, payload (mode, last_id, complete), bytes, per-field usage
//!   serve [--index DIR]        JSON-lines requests on stdin, one JSON response line each:
//!       {"op":"search", "configs":[{"config","match":SPEC,"direct":SPEC}], "filters":{...}, "budget":N}
//!       {"op":"websearch"|"phraseto", "config":C, "q":Q}     native tsquery text
//!       {"op":"tsvector", "config":C, "context":X, "text":T}  native passage tsvector
//!       {"op":"parse_tsquery", "tsquery":T}                  normalised tsquery text
//!       {"op":"info"}
//!       {"op":"has", "ids":[uuid, ...]}                     the ids present in the index
//! See ulis/retrieval/tantivy_engine.py for the Python side.

mod analyzer;
mod index;
mod search;
mod tsquery;

use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::path::PathBuf;

fn arg(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned()
}

fn flag(args: &[String], name: &str) -> bool {
    args.iter().any(|a| a == name)
}

fn handle(engine: &Option<search::Engine>, index_path: &Option<PathBuf>, req: &Value) -> Result<Value, String> {
    let op = req.get("op").and_then(|v| v.as_str()).unwrap_or("search");
    let s = |k: &str| req.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
    match op {
        "search" => engine.as_ref().ok_or("serve was started without --index")?.search(req),
        "websearch" => Ok(json!({"tsquery": tsquery::to_text(&tsquery::websearch(analyzer::config(&s("config")), &s("q")))})),
        "phraseto" => Ok(json!({"tsquery": tsquery::to_text(&tsquery::phraseto(analyzer::config(&s("config")), &s("q")))})),
        "parse_tsquery" => Ok(json!({"tsquery": tsquery::to_text(&tsquery::parse_text(&s("tsquery"))?)})),
        "tsvector" => {
            let tv = analyzer::passage_tsvector(analyzer::config(&s("config")), &s("context"), &s("text"));
            let map: serde_json::Map<String, Value> = tv
                .into_iter()
                .map(|(k, v)| (k, json!(v.into_iter().map(|(p, a)| json!([p, if a { "A" } else { "B" }])).collect::<Vec<_>>())))
                .collect();
            Ok(json!({"tsvector": map}))
        }
        "info" => index::info(index_path.as_ref().ok_or("no index")?),
        "has" => {
            let ids: Vec<String> = req
                .get("ids")
                .and_then(|v| v.as_array())
                .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
                .unwrap_or_default();
            engine.as_ref().ok_or("serve was started without --index")?.has(&ids)
        }
        other => Err(format!("unknown op {other:?}")),
    }
}

fn serve(index_path: Option<PathBuf>) -> Result<(), String> {
    let engine = match &index_path {
        Some(p) => Some(search::Engine::open(p)?),
        None => None,
    };
    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    if let Some(e) = &engine {
        writeln!(out, "{}", json!({"ok": true, "ready": true, "mode": e.mode})).map_err(|e| e.to_string())?;
    } else {
        writeln!(out, "{}", json!({"ok": true, "ready": true, "mode": null})).map_err(|e| e.to_string())?;
    }
    out.flush().map_err(|e| e.to_string())?;
    for line in stdin.lock().lines() {
        let line = line.map_err(|e| e.to_string())?;
        if line.trim().is_empty() {
            continue;
        }
        let resp = match serde_json::from_str::<Value>(&line) {
            Ok(req) => match handle(&engine, &index_path, &req) {
                Ok(mut v) => {
                    v["ok"] = json!(true);
                    v
                }
                Err(e) => json!({"ok": false, "error": e}),
            },
            Err(e) => json!({"ok": false, "error": format!("bad request: {e}")}),
        };
        writeln!(out, "{resp}").map_err(|e| e.to_string())?;
        out.flush().map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn run() -> Result<(), String> {
    let args: Vec<String> = std::env::args().collect();
    let cmd = args.get(1).map(String::as_str).unwrap_or("");
    let index_path = arg(&args, "--index").map(PathBuf::from);
    match cmd {
        "build" => {
            let path = index_path.ok_or("--index DIR required")?;
            let mode = arg(&args, "--mode").ok_or("--mode pg|native required")?;
            let opts = index::BuildOptions {
                heap_mb: arg(&args, "--heap-mb").map(|v| v.parse().unwrap_or(1000)).unwrap_or(1000),
                threads: arg(&args, "--threads").map(|v| v.parse().unwrap_or(2)).unwrap_or(2),
                commit_every: arg(&args, "--commit-every").map(|v| v.parse().unwrap_or(100_000)).unwrap_or(100_000).max(1),
                merge: flag(&args, "--merge"),
                progress: flag(&args, "--progress"),
            };
            println!("{}", index::build(&path, &mode, &opts)?);
        }
        "merge" => println!("{}", index::merge(&index_path.ok_or("--index DIR required")?)?),
        "info" => println!("{}", index::info(&index_path.ok_or("--index DIR required")?)?),
        "serve" => serve(index_path)?,
        _ => return Err("usage: ulis-tantivy build|merge|info|serve [--index DIR] (see src/main.rs)".into()),
    }
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        eprintln!("ulis-tantivy: {e}");
        std::process::exit(1);
    }
}
