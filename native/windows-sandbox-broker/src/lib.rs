#[cfg_attr(not(windows), allow(dead_code))]
mod protocol;

#[cfg_attr(not(windows), allow(dead_code))]
mod job_guard;

#[cfg_attr(not(windows), allow(dead_code))]
mod path_guard;

#[cfg_attr(not(windows), allow(dead_code))]
mod startup;

#[cfg(windows)]
mod acl_guard;

#[cfg(windows)]
mod windows;

#[cfg(windows)]
pub fn run() {
    windows::run();
}
