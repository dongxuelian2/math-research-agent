use super::*;

impl App {
    pub(super) fn start_backend(&mut self, verb: &str, subject: &str, mut args: Vec<String>) {
        let project = self.project.clone();
        if self.running.contains_key(&project) {
            self.log("System · 当前项目已有任务运行中；可用 /stop 停止。");
            return;
        }
        if matches!(
            args.first().map(String::as_str),
            Some("orchestrate" | "add-file")
        ) {
            args.push("--ui-events".into());
        }
        let activity = format!("{verb} · {}", short_activity(subject));
        let root = self.root.clone();
        append_diagnostic(
            &project,
            false,
            &format!(
                "$ uv run --project {:?} python -m math_research_agent.research {:?}",
                root, args
            ),
        );
        let child_handle = Arc::new(Mutex::new(None));
        let cancelled = Arc::new(AtomicBool::new(false));
        self.running.insert(
            project.clone(),
            RunningTask {
                started_at: Instant::now(),
                child: child_handle.clone(),
                cancelled: cancelled.clone(),
            },
        );
        self.session_mut().entry(
            TranscriptKind::Activity,
            format!("已启动：{}", short_activity(subject)),
            true,
        );
        let tx = self.tx.clone();
        thread::spawn(move || {
            let mut backend_command = Command::new("uv");
            backend_command
                .current_dir(&root)
                .args(["run", "--project"])
                .arg(&root)
                .args(["python", "-m", "math_research_agent.research"])
                .args(&args)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            if env::var_os("UV_CACHE_DIR").is_none() {
                backend_command.env("UV_CACHE_DIR", "/tmp/math-agent-uv-cache");
            }
            configure_process_group(&mut backend_command);
            let spawn = backend_command.spawn();
            let mut child = match spawn {
                Ok(child) => child,
                Err(error) => {
                    let _ = tx.send(BackendEvent::Line {
                        project: project.clone(),
                        line: format!("无法启动研究进程：{error}"),
                        stderr: true,
                    });
                    let _ = tx.send(BackendEvent::Finished {
                        project,
                        activity,
                        success: false,
                        cancelled: false,
                    });
                    return;
                }
            };
            let stdout = child.stdout.take();
            let stderr = child.stderr.take();
            *child_handle.lock().expect("child lock") = Some(child);
            if cancelled.load(Ordering::SeqCst) {
                if let Some(child) = child_handle.lock().expect("child lock").as_mut() {
                    let _ = stop_child(child);
                }
            }
            let out_tx = tx.clone();
            let out_project = project.clone();
            let stdout_thread = thread::spawn(move || {
                if let Some(stdout) = stdout {
                    for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                        let _ = out_tx.send(BackendEvent::Line {
                            project: out_project.clone(),
                            line,
                            stderr: false,
                        });
                    }
                }
            });
            let err_tx = tx.clone();
            let err_project = project.clone();
            let stderr_thread = thread::spawn(move || {
                if let Some(stderr) = stderr {
                    for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                        let _ = err_tx.send(BackendEvent::Line {
                            project: err_project.clone(),
                            line,
                            stderr: true,
                        });
                    }
                }
            });
            let status = loop {
                let result = child_handle
                    .lock()
                    .expect("child lock")
                    .as_mut()
                    .and_then(|child| child.try_wait().ok())
                    .flatten();
                if let Some(status) = result {
                    break status;
                }
                thread::sleep(Duration::from_millis(50));
            };
            let _ = stdout_thread.join();
            let _ = stderr_thread.join();
            let _ = tx.send(BackendEvent::Finished {
                project,
                activity,
                success: status.success(),
                cancelled: cancelled.load(Ordering::SeqCst),
            });
        });
    }

    pub(super) fn stop_backend(&mut self) {
        let Some(task) = self.running.get(&self.project) else {
            self.log("System · 当前项目没有运行中的证明。");
            return;
        };
        task.cancelled.store(true, Ordering::SeqCst);
        let result = task
            .child
            .lock()
            .expect("child lock")
            .as_mut()
            .map(stop_child);
        match result {
            Some(Ok(())) => self.log("System · 已发送停止请求，等待进程退出…"),
            Some(Err(error)) => self.log(format!("System · 停止失败：{error}")),
            None => self.log("System · 任务正在启动，已标记为停止。"),
        }
    }

    pub(super) fn drain_backend_events(&mut self) {
        while let Ok(event) = self.rx.try_recv() {
            match event {
                BackendEvent::Line {
                    project,
                    line,
                    stderr,
                } => {
                    append_diagnostic(&project, stderr, &line);
                    if let Some(session) = self.sessions.get_mut(&project) {
                        if let Ok(value) = serde_json::from_str::<Value>(&line) {
                            if value.get("event_type").is_some() {
                                session.apply_ui_event(&value);
                            } else if !stderr {
                                session.entry(TranscriptKind::Output, line, false);
                            } else {
                                session.entry(TranscriptKind::Error, line, false);
                            }
                        } else if stderr {
                            // Keep raw diagnostics out of the compact activity
                            // stream; they remain available in full transcript.
                            session.entry(TranscriptKind::Error, line, false);
                        } else {
                            session.entry(TranscriptKind::Output, line, false);
                        }
                    }
                }
                BackendEvent::Finished {
                    project,
                    activity,
                    success,
                    cancelled,
                } => {
                    append_diagnostic(
                        &project,
                        !success,
                        &format!(
                            "进程结束：{}{}",
                            if success { "成功" } else { "失败" },
                            if cancelled { "（已停止）" } else { "" }
                        ),
                    );
                    self.running.remove(&project);
                    if let Some(session) = self.sessions.get_mut(&project) {
                        let kind = if cancelled {
                            TranscriptKind::Warning
                        } else if success {
                            TranscriptKind::Success
                        } else {
                            TranscriptKind::Failure
                        };
                        session.entry(
                            kind,
                            format!(
                                "{}：{}",
                                if cancelled {
                                    "已停止"
                                } else if success {
                                    "运行完成"
                                } else {
                                    "运行失败"
                                },
                                short_activity(&activity)
                            ),
                            true,
                        );
                        session.snapshot = read_snapshot(&project);
                        // A killed CLI may not get a chance to finalize
                        // project.json. Keep the visible session truthful even
                        // when the durable project projection is from an older
                        // run.
                        if !success && !cancelled {
                            session.snapshot.orchestrator_status = "FAILED".to_string();
                        } else if success && session.snapshot.orchestrator_status == "RUNNING" {
                            session.snapshot.orchestrator_status = "COMPLETED".to_string();
                        }
                    }
                }
            }
        }
    }
}

#[cfg(unix)]
fn configure_process_group(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    command.process_group(0);
}

#[cfg(not(unix))]
fn configure_process_group(_command: &mut Command) {}

#[cfg(unix)]
pub(super) fn stop_child(child: &mut Child) -> io::Result<()> {
    let group = format!("-{}", child.id());
    let status = Command::new("kill")
        .args(["-TERM", "--", group.as_str()])
        .status()?;
    if status.success() {
        Ok(())
    } else {
        child.kill()
    }
}

#[cfg(not(unix))]
pub(super) fn stop_child(child: &mut Child) -> io::Result<()> {
    child.kill()
}
