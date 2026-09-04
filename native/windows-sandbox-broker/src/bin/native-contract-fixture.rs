#[cfg(not(windows))]
fn main() {
    eprintln!("native-contract-fixture is only supported on Windows");
    std::process::exit(2);
}

#[cfg(windows)]
fn main() {
    use std::io::{self, Write};
    use std::net::{SocketAddr, TcpStream};
    use std::path::Path;
    use std::time::Duration;

    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    let command = arguments.first().map(String::as_str).unwrap_or_default();
    match command {
        "write" => {
            let Some(path) = arguments.get(1) else {
                std::process::exit(64);
            };
            match std::fs::write(Path::new(path), b"fixture output\n") {
                Ok(()) => println!("wrote"),
                Err(error) => {
                    eprintln!("write failed: {error}");
                    std::process::exit(41);
                }
            }
        }
        "read" => {
            let Some(path) = arguments.get(1) else {
                std::process::exit(64);
            };
            match std::fs::read(Path::new(path)) {
                Ok(bytes) => {
                    io::stdout().write_all(&bytes).expect("fixture stdout");
                }
                Err(error) => {
                    eprintln!("read failed: {error}");
                    std::process::exit(41);
                }
            }
        }
        "stdout" => {
            let Some(requested) = arguments.get(1) else {
                std::process::exit(64);
            };
            let Ok(requested) = requested.parse::<usize>() else {
                std::process::exit(64);
            };
            let mut stdout = io::stdout().lock();
            let chunk = [b'x'; 1024];
            let mut remaining = requested;
            while remaining > 0 {
                let count = remaining.min(chunk.len());
                stdout.write_all(&chunk[..count]).expect("fixture stdout");
                remaining -= count;
            }
            stdout.flush().expect("fixture stdout flush");
        }
        "sleep" => {
            let Some(milliseconds) = arguments.get(1) else {
                std::process::exit(64);
            };
            let Ok(milliseconds) = milliseconds.parse::<u64>() else {
                std::process::exit(64);
            };
            std::thread::sleep(Duration::from_millis(milliseconds));
        }
        "connect" => {
            let Some(address) = arguments.get(1) else {
                std::process::exit(64);
            };
            let Ok(address) = address.parse::<SocketAddr>() else {
                std::process::exit(64);
            };
            match TcpStream::connect_timeout(&address, Duration::from_millis(500)) {
                Ok(_) => {
                    println!("connected");
                }
                Err(error) => {
                    eprintln!("connect failed: {error}");
                    std::process::exit(41);
                }
            }
        }
        _ => std::process::exit(64),
    }
}
