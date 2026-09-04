#[cfg(not(windows))]
fn main() {
    eprintln!("ocean-sandbox-broker is only supported on native Windows");
    std::process::exit(2);
}

#[cfg(windows)]
fn main() {
    ocean_windows_sandbox_broker::run();
}
