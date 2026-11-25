use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

struct Install(PathBuf);

impl Drop for Install {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

impl Install {
    fn new(test: &str) -> Self {
        let root = Self(
            std::env::temp_dir().join(format!("infamous-update-{test}-{}", std::process::id())),
        );
        fs::create_dir_all(root.0.join("installer")).unwrap();
        fs::copy(
            concat!(env!("CARGO_MANIFEST_DIR"), "/infamous.toml"),
            root.0.join("installer/infamous.toml"),
        )
        .unwrap();
        root
    }

    fn update(&self, api: &str) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_infamous-installer"));
        command
            .args(["update", "--root"])
            .arg(&self.0)
            .env("INFAMOUS_UPDATE_API", api)
            .env("INFAMOUS_UPDATE_RAW", api)
            .env("NO_PROXY", "127.0.0.1")
            .env_remove("GITHUB_TOKEN");
        command
    }
}

const API_REPO: &str = "/repos/WoofahRayetCode/inFAMOUS-US";
const COMMIT: &str = "2222222222222222222222222222222222222222";
const TREE_PATH: &str = "/git/trees/2222222222222222222222222222222222222222?recursive=1";

fn serve(responses: Vec<(&'static str, String)>) -> (String, std::thread::JoinHandle<()>) {
    let server = TcpListener::bind("127.0.0.1:0").unwrap();
    server.set_nonblocking(true).unwrap();
    let api = format!("http://{}", server.local_addr().unwrap());
    let serve = std::thread::spawn(move || {
        for (path, body) in responses {
            let deadline = std::time::Instant::now() + Duration::from_secs(15);
            let mut stream = loop {
                match server.accept() {
                    Ok((stream, _)) => break stream,
                    Err(err)
                        if err.kind() == std::io::ErrorKind::WouldBlock
                            && std::time::Instant::now() < deadline =>
                    {
                        std::thread::sleep(Duration::from_millis(10));
                    }
                    Err(err) => panic!("expected request for {path}: {err}"),
                }
            };
            stream.set_nonblocking(false).unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut request = BufReader::new(&stream);
            let mut line = String::new();
            request.read_line(&mut line).unwrap();
            assert_eq!(line.trim_end(), format!("GET {API_REPO}{path} HTTP/1.1"));
            line.clear();
            while request.read_line(&mut line).unwrap() != 0 && line != "\r\n" {
                line.clear();
            }
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            )
            .unwrap();
            stream.write_all(body.as_bytes()).unwrap();
        }
    });
    (api, serve)
}

const TREE: &str = r#"{"tree":[{"path":"installer/infamous.toml","type":"blob","sha":"abc"}]}"#;
#[test]
fn check_describes_same_commit_file_repair_and_preserves_the_legacy_status_line() {
    let root = Install::new("repair-check");
    fs::create_dir(root.0.join(".installer")).unwrap();
    fs::write(
        root.0.join(".installer/update.state"),
        format!("commit {COMMIT}\n"),
    )
    .unwrap();
    let (api, server) = serve(vec![
        ("/commits/main", COMMIT.into()),
        (TREE_PATH, TREE.into()),
    ]);
    let output = root
        .update(&api)
        .args(["--branch", "main", "--check"])
        .output()
        .unwrap();
    assert!(output.status.success(), "{output:?}");
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("This version is already installed."),
        "{stdout}"
    );
    assert!(stdout
        .lines()
        .any(|line| line == format!("UPDATE-CHECK available {COMMIT} 1 0")));
    assert!(!root.0.join(".installer/update.pending").exists());
    server.join().unwrap();
}

#[test]
fn update_rejects_a_branch_tip_that_changed_after_review() {
    let root = Install::new("review");
    let (api, serve) = serve(vec![
        ("/commits/main", COMMIT.into()),
        (TREE_PATH, TREE.into()),
    ]);
    let output = root
        .update(&api)
        .args([
            "--force",
            "--branch",
            "main",
            "--expect-commit",
            "1111111111111111111111111111111111111111",
        ])
        .output()
        .unwrap();

    let error = String::from_utf8_lossy(&output.stderr);
    assert!(!output.status.success());
    assert!(error.contains("changed since review"), "{error}");
    assert!(!root.0.join(".installer").exists());
    serve.join().unwrap();
}
