use super::*;
use eframe::egui::{self, Color32, RichText};
use infamous_installer::{game, lock, manifest::Manifest, setup, trophy};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc, Arc,
};

const BACKGROUND: Color32 = Color32::from_rgb(16, 19, 25);
const PANEL: Color32 = Color32::from_rgb(24, 30, 39);
const BORDER: Color32 = Color32::from_rgb(47, 58, 73);
const MUTED: Color32 = Color32::from_rgb(159, 174, 193);
const BLUE: Color32 = Color32::from_rgb(105, 197, 255);
const AMBER: Color32 = Color32::from_rgb(255, 199, 107);

#[derive(Clone, Copy, PartialEq)]
enum Tab {
    Play,
    Settings,
    Saves,
    Achievements,
    Support,
}

struct Dashboard {
    state: State,
    tab: Tab,
    played: Arc<AtomicBool>,
    feature_check: Option<mpsc::Receiver<Features>>,
    banner: Option<egui::TextureHandle>,
    region: Option<String>,
    notice: Option<(String, bool)>,
    saves: Result<Vec<save_manager::Entry>, String>,
    backups: Result<Vec<save_manager::Entry>, String>,
    backup: Option<String>,
    scope: Scope,
    restore: Option<(String, Scope)>,
    trophy_catalog: Option<Result<trophy::Catalog, String>>,
    trophy_progress: Result<BTreeMap<u32, u64>, String>,
    update_check: Option<mpsc::Receiver<Result<setup::UpdateInfo, String>>>,
    update_result: Option<Result<setup::UpdateInfo, String>>,
}

use infamous_installer::save_manager;

fn card(ui: &mut egui::Ui, add: impl FnOnce(&mut egui::Ui)) {
    egui::Frame::none()
        .fill(PANEL)
        .stroke(egui::Stroke::new(1.0_f32, BORDER))
        .rounding(8.0)
        .inner_margin(18.0)
        .show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            add(ui);
        });
}

fn muted(ui: &mut egui::Ui, text: impl Into<String>) {
    ui.label(RichText::new(text.into()).color(MUTED));
}

fn scope_name(scope: Scope) -> &'static str {
    match scope {
        Scope::Saves => "Saves only",
        Scope::Settings => "Settings only",
        Scope::Both => "Saves and settings",
    }
}

impl Dashboard {
    fn new(state: State, played: Arc<AtomicBool>, ctx: &egui::Context) -> Self {
        ctx.set_theme(egui::Theme::Dark);
        let mut visuals = egui::Visuals::dark();
        visuals.panel_fill = BACKGROUND;
        visuals.window_fill = PANEL;
        visuals.window_stroke = egui::Stroke::new(1.0_f32, BORDER);
        visuals.override_text_color = Some(Color32::from_rgb(231, 238, 248));
        visuals.selection.bg_fill = Color32::from_rgb(29, 80, 116);
        visuals.hyperlink_color = BLUE;
        visuals.widgets.inactive.bg_fill = Color32::from_rgb(32, 41, 53);
        visuals.widgets.inactive.weak_bg_fill = visuals.widgets.inactive.bg_fill;
        visuals.widgets.hovered.bg_fill = Color32::from_rgb(42, 57, 75);
        visuals.widgets.hovered.weak_bg_fill = visuals.widgets.hovered.bg_fill;
        ctx.set_visuals(visuals);
        ctx.style_mut(|style| {
            style.spacing.item_spacing = egui::vec2(10.0, 9.0);
            style.spacing.button_padding = egui::vec2(12.0, 7.0);
            style.spacing.interact_size.y = 28.0;
            for text_style in [egui::TextStyle::Body, egui::TextStyle::Button] {
                if let Some(font) = style.text_styles.get_mut(&text_style) {
                    font.size = 15.0;
                }
            }
        });
        let feature_check = if state.features.is_none() {
            let (send, receive) = mpsc::channel();
            let exe = state.launch.exe.clone();
            let cache = state.launch.cache_path();
            let ctx = ctx.clone();
            std::thread::spawn(move || {
                let _ = send.send(scan_features(&exe, &cache));
                ctx.request_repaint();
            });
            Some(receive)
        } else {
            None
        };
        let region = state
            .launch
            .args
            .first()
            .and_then(|p| p.parent()?.parent())
            .and_then(|p| game::read_sfo(&p.join("PARAM.SFO")).ok())
            .and_then(|mut sfo| sfo.remove("TITLE_ID"));
        let tab = if state.launch.settings_only {
            Tab::Settings
        } else {
            Tab::Play
        };
        Self {
            state,
            tab,
            played,
            feature_check,
            banner: banner_texture(ctx),
            region,
            notice: None,
            saves: Ok(Vec::new()),
            backups: Ok(Vec::new()),
            backup: None,
            scope: Scope::Saves,
            restore: None,
            trophy_catalog: None,
            trophy_progress: Ok(BTreeMap::new()),
            update_check: None,
            update_result: None,
        }
    }

    fn region_label(&self) -> &str {
        match self.region.as_deref() {
            Some("BCUS98119") => "US / BCUS98119",
            Some("BCES00220") => "Europe / BCES00220",
            Some(other) => other,
            None => "Region unavailable",
        }
    }

    fn readiness(&self) -> Result<&'static str, &'static str> {
        if self.state.launch.settings_only {
            return Ok("Settings editor");
        }
        if !self.state.launch.exe.is_file() {
            return Err("Game build missing. Run inFamous Setup to repair it.");
        }
        if self.state.launch.args.iter().any(|p| !p.is_file()) {
            return Err("Game input missing. Reconnect the disc folder or run Setup.");
        }
        Ok("Game build ready")
    }

    fn summary(&self) -> String {
        format!(
            "{}   /   {}   /   {}",
            DISPLAY[2].names[self.state.get(&DISPLAY[2])],
            DISPLAY[4].names[self.state.get(&DISPLAY[4])],
            DISPLAY[0].names[self.state.get(&DISPLAY[0])]
        )
    }

    fn setting(&mut self, ui: &mut egui::Ui, row: &Row) {
        let mut selected = self.state.get(row);
        ui.horizontal(|ui| {
            ui.add_sized(
                [145.0, 0.0],
                egui::Label::new(RichText::new(row.label).strong()),
            )
            .on_hover_text(row.hint);
            egui::ComboBox::from_id_salt(row.key)
                .selected_text(row.names[selected])
                .width(175.0)
                .show_ui(ui, |ui| {
                    for (i, name) in row.names.iter().enumerate() {
                        ui.selectable_value(&mut selected, i, *name);
                    }
                })
                .response
                .on_hover_text(row.hint);
        });
        if selected != self.state.get(row) {
            self.state.cur.insert(row.key, selected);
        }
    }

    fn home(&mut self, ui: &mut egui::Ui) {
        let banner_height = (ui.ctx().screen_rect().height() * 0.37).clamp(200.0, 285.0);
        let (rect, _) = ui.allocate_exact_size(
            egui::vec2(ui.available_width(), banner_height),
            egui::Sense::hover(),
        );
        let painter = ui.painter_at(rect);
        painter.rect_filled(rect, 8.0, Color32::from_rgb(19, 42, 64));
        if let Some(banner) = &self.banner {
            let ratio = rect.width() / rect.height();
            let texture_ratio = banner.size_vec2().x / banner.size_vec2().y;
            let width = (ratio / texture_ratio).min(1.0);
            let height = (texture_ratio / ratio).min(1.0);
            let mid = egui::pos2(0.5, 0.5);
            painter.image(
                banner.id(),
                rect,
                egui::Rect::from_center_size(mid, egui::vec2(width, height)),
                Color32::WHITE,
            );
        }
        let mut shade = egui::Mesh::default();
        shade.colored_vertex(rect.left_top(), Color32::from_black_alpha(216));
        shade.colored_vertex(rect.right_top(), Color32::from_black_alpha(22));
        shade.colored_vertex(rect.right_bottom(), Color32::from_black_alpha(87));
        shade.colored_vertex(rect.left_bottom(), Color32::from_black_alpha(226));
        shade.add_triangle(0, 1, 2);
        shade.add_triangle(0, 2, 3);
        painter.add(egui::Shape::mesh(shade));
        painter.rect_filled(
            egui::Rect::from_min_size(
                rect.left_top() + egui::vec2(25.0, 47.0),
                egui::vec2(28.0, 3.0),
            ),
            1.0,
            BLUE,
        );
        painter.text(
            rect.left_top() + egui::vec2(25.0, 57.0),
            egui::Align2::LEFT_TOP,
            "inFamous",
            egui::FontId::proportional(50.0),
            Color32::WHITE,
        );
        painter.text(
            rect.left_top() + egui::vec2(28.0, 130.0),
            egui::Align2::LEFT_TOP,
            self.region_label(),
            egui::FontId::monospace(14.0),
            BLUE,
        );

        ui.add_space(8.0);
        card(ui, |ui| {
            ui.label(RichText::new("Installation").size(19.0).strong());
            let (status, color) = match self.readiness() {
                Ok(s) => (s, BLUE),
                Err(s) => (s, AMBER),
            };
            ui.label(RichText::new(status).color(color));
        });
        card(ui, |ui| {
            ui.label(RichText::new("Quick settings").size(19.0).strong());
            ui.columns(2, |cols| {
                self.setting(&mut cols[0], &DISPLAY[0]);
                self.setting(&mut cols[1], &DISPLAY[4]);
            });
        });
    }

    fn settings(&mut self, ui: &mut egui::Ui) {
        ui.heading("Display & Controls");
        if self.feature_check.is_some() {
            muted(
                ui,
                "Checking which graphics features this build supports...",
            );
        }
        for (title, rows) in ["Display", "Graphics", "Controls", "Game"]
            .into_iter()
            .zip(self.state.groups())
        {
            card(ui, |ui| {
                ui.label(RichText::new(title).size(19.0).strong());
                for row in rows {
                    self.setting(ui, row);
                }
            });
        }
        if ui.button("Reset to defaults").clicked() {
            self.state.restore_defaults();
        }
    }

    fn refresh_saves(&mut self) {
        let manager = self.state.launch.save_manager();
        self.saves = manager.list_saves().map_err(|e| e.to_string());
        self.backups = manager.list_backups().map_err(|e| e.to_string());
        if let Ok(entries) = &self.backups {
            if self
                .backup
                .as_ref()
                .is_none_or(|name| !entries.iter().any(|e| &e.name == name))
            {
                self.backup = entries.first().map(|e| e.name.clone());
            }
        } else {
            self.backup = None;
        }
    }

    fn notify(&mut self, result: Result<String, String>) {
        self.notice = Some(match result {
            Ok(message) => (message, false),
            Err(message) => (message, true),
        });
    }

    fn open_data_folder(&mut self, path: &Path) {
        match std::fs::create_dir_all(path) {
            Ok(()) => setup::open_folder(path),
            Err(error) => self.notify(Err(format!("Could not open folder: {error}"))),
        }
    }

    fn backup(&mut self, scope: Scope) {
        let result = (|| {
            let _lock = GameLock::acquire().map_err(|e| e.to_string())?;
            self.state
                .launch
                .save_manager()
                .backup(scope)
                .map_err(|e| e.to_string())?;
            Ok("Backup created.".into())
        })();
        self.notify(result);
        self.refresh_saves();
    }

    fn restore_backup(&mut self, name: &str, scope: Scope) {
        let result = (|| {
            let _lock = GameLock::acquire().map_err(|e| e.to_string())?;
            self.state
                .launch
                .save_manager()
                .restore(name, scope)
                .map_err(|e| e.to_string())?;
            self.state = State::new_for_restore(&self.state);
            Ok("Restore complete. Recovery backup created.".into())
        })();
        self.notify(result);
        self.refresh_saves();
    }

    fn saves(&mut self, ui: &mut egui::Ui) {
        let manager = self.state.launch.save_manager();
        ui.heading("Saves");
        card(ui, |ui| {
            ui.label(RichText::new("Game saves").size(19.0).strong());
            muted(ui, manager.saves.display().to_string());
            match &self.saves {
                Ok(entries) if entries.is_empty() => muted(ui, "No inFamous saves found."),
                Ok(entries) => {
                    egui::Grid::new("save_rows").striped(true).show(ui, |ui| {
                        ui.strong("Save");
                        ui.strong("Modified (UTC)");
                        ui.end_row();
                        for entry in entries {
                            ui.label(&entry.name);
                            ui.label(&entry.modified);
                            ui.end_row();
                        }
                    });
                }
                Err(error) => {
                    ui.colored_label(AMBER, format!("Could not read saves: {error}"));
                }
            }
            ui.horizontal_wrapped(|ui| {
                if ui.button("Open save folder").clicked() {
                    self.open_data_folder(&manager.saves);
                }
                if ui.button("Refresh").clicked() {
                    self.refresh_saves();
                }
            });
        });
        card(ui, |ui| {
            ui.label(RichText::new("Backups").size(19.0).strong());
            let dirty = self.state.dirty();
            ui.horizontal_wrapped(|ui| {
                for (label, scope) in [
                    ("Back up saves", Scope::Saves),
                    ("Back up settings", Scope::Settings),
                    ("Back up everything", Scope::Both),
                ] {
                    if ui
                        .add_enabled(
                            !dirty || matches!(scope, Scope::Saves),
                            egui::Button::new(label),
                        )
                        .clicked()
                    {
                        self.backup(scope);
                    }
                }
                if ui.button("Open backup folder").clicked() {
                    self.open_data_folder(&manager.backups);
                }
            });
            if dirty {
                muted(
                    ui,
                    "Save your pending settings changes before backing up settings or restoring.",
                );
            }
            match &self.backups {
                Ok(entries) if !entries.is_empty() => {
                    egui::ComboBox::from_id_salt("backup")
                        .selected_text(self.backup.as_deref().unwrap_or("Select backup"))
                        .show_ui(ui, |ui| {
                            for entry in entries {
                                ui.selectable_value(
                                    &mut self.backup,
                                    Some(entry.name.clone()),
                                    format!("{}  /  {}", entry.modified, entry.name),
                                );
                            }
                        });
                    egui::ComboBox::from_id_salt("restore scope")
                        .selected_text(scope_name(self.scope))
                        .show_ui(ui, |ui| {
                            for scope in [Scope::Saves, Scope::Settings, Scope::Both] {
                                if ui
                                    .selectable_label(
                                        scope_name(scope) == scope_name(self.scope),
                                        scope_name(scope),
                                    )
                                    .clicked()
                                {
                                    self.scope = scope;
                                }
                            }
                        });
                    if ui
                        .add_enabled(!dirty, egui::Button::new("Restore selected backup..."))
                        .clicked()
                    {
                        if let Some(name) = self.backup.clone() {
                            self.restore = Some((name, self.scope));
                        }
                    }
                }
                Ok(_) => muted(ui, "No backups yet."),
                Err(error) => {
                    ui.colored_label(AMBER, format!("Could not read backups: {error}"));
                }
            }
        });
    }

    fn refresh_achievements(&mut self) {
        let result = (|| {
            let eboot = self
                .state
                .launch
                .args
                .first()
                .ok_or("No game dump configured.")?;
            let disc = game::locate(eboot).map_err(|e| e.to_string())?;
            trophy::read_catalog(&disc.root).map_err(|e| e.to_string())
        })();
        self.trophy_progress = match &result {
            Ok(catalog) => catalog
                .unlocked(&self.state.launch.cwd)
                .map_err(|e| e.to_string()),
            Err(_) => Ok(BTreeMap::new()),
        };
        self.trophy_catalog = Some(result);
    }

    fn achievements(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.heading("Achievements");
            if ui.button("Refresh progress").clicked() {
                self.refresh_achievements();
            }
        });
        let catalog = match &self.trophy_catalog {
            Some(Ok(catalog)) => catalog,
            Some(Err(error)) => {
                ui.colored_label(
                    AMBER,
                    format!("Could not read the disc's trophies: {error}"),
                );
                return;
            }
            None => return,
        };
        let unlocked = match &self.trophy_progress {
            Ok(unlocked) => unlocked,
            Err(error) => {
                ui.colored_label(
                    AMBER,
                    format!("Could not read local trophy progress: {error}"),
                );
                return;
            }
        };
        let total = catalog.trophies.len();
        let earned = catalog
            .trophies
            .iter()
            .filter(|t| unlocked.contains_key(&t.id))
            .count();
        card(ui, |ui| {
            ui.label(RichText::new(format!("{earned} of {total} trophies earned")).strong());
            ui.add(egui::ProgressBar::new(earned as f32 / total as f32));
        });
        card(ui, |ui| {
            for t in &catalog.trophies {
                let earned = unlocked.contains_key(&t.id);
                let (grade, color) = match t.grade {
                    'P' => ("Platinum", BLUE),
                    'G' => ("Gold", AMBER),
                    'S' => ("Silver", Color32::from_rgb(200, 217, 232)),
                    _ => ("Bronze", Color32::from_rgb(229, 159, 116)),
                };
                ui.horizontal_wrapped(|ui| {
                    ui.colored_label(color, grade);
                    ui.strong(if t.hidden && !earned {
                        "Hidden trophy"
                    } else {
                        &t.name
                    });
                    muted(ui, if earned { "Earned" } else { "Locked" });
                });
                if !t.hidden || earned {
                    if !t.detail.is_empty() {
                        muted(ui, t.detail.clone());
                    }
                }
                ui.separator();
            }
        });
    }

    fn support(&mut self, ui: &mut egui::Ui) {
        ui.heading("Support");
        card(ui, |ui| {
            ui.label(RichText::new("Game logs").size(19.0).strong());
            let launch = &self.state.launch;
            let mut log = None;
            for (name, base) in [
                ("Output", launch.out.as_deref()),
                ("Errors", launch.err.as_deref()),
            ] {
                if let Some(base) = base {
                    let current = resolve(&launch.cwd, base);
                    let previous = current.with_extension("prev.log");
                    ui.horizontal_wrapped(|ui| {
                        for (label, path) in [
                            (format!("Current {name}"), current),
                            (format!("Previous {name}"), previous),
                        ] {
                            if ui
                                .add_enabled(path.is_file(), egui::Button::new(label))
                                .clicked()
                            {
                                log = Some(path);
                            }
                        }
                    });
                }
            }
            if let Some(path) = log {
                if let Err(e) = Command::new("notepad.exe").arg(path).spawn() {
                    self.notify(Err(format!("Could not open log: {e}")));
                }
            }
            if ui.button("Open log folder").clicked() {
                setup::open_folder(&self.state.launch.cwd);
            }
        });
        card(ui, |ui| {
            ui.label(RichText::new("Build & updates").size(19.0).strong());
            ui.label(self.region_label());
            if ui.button("Copy build info").clicked() {
                let root = &self.state.launch.root;
                let install = lock::State::load(&root.join(".installer/state"));
                let commit = std::fs::read_to_string(root.join(".installer/update.state"))
                    .ok()
                    .and_then(|t| {
                        t.lines()
                            .find_map(|l| l.strip_prefix("commit ").map(str::to_owned))
                    });
                let build =
                    exe_stamp(&self.state.launch.exe).unwrap_or_else(|| "Unavailable".into());
                let settings = DISPLAY
                    .iter()
                    .map(|r| format!("{}: {}", r.label, r.names[self.state.get(r)]))
                    .collect::<Vec<_>>()
                    .join("\n");
                ui.ctx().copy_text(format!(
                    "inFamous PC\nRegion: {}\nUpdater commit: {}\nGame executable (bytes and modified Unix time): {}\nEBOOT SHA-256: {}\n{settings}",
                    self.region_label(), commit.as_deref().unwrap_or("Unavailable"), build,
                    install.eboot.as_deref().unwrap_or("Unavailable")));
                self.notify(Ok("Build info copied.".into()));
            }
            ui.horizontal_wrapped(|ui| {
                let installed = !self.state.launch.settings_only
                    && setup::is_installed(&self.state.launch.root);
                if ui
                    .add_enabled(
                        installed && self.update_check.is_none(),
                        egui::Button::new("Check GitHub for updates"),
                    )
                    .clicked()
                {
                    let root = self.state.launch.root.clone();
                    let ctx = ui.ctx().clone();
                    let (send, receive) = mpsc::channel();
                    self.update_result = None;
                    self.update_check = Some(receive);
                    std::thread::spawn(move || {
                        let result = Manifest::load(&root.join("installer/infamous.toml"))
                            .map_err(|e| e.to_string())
                            .and_then(|m| {
                                setup::check_update(
                                    &root,
                                    m.update_repo.as_deref(),
                                    &m.update_branch,
                                )
                            });
                        let _ = send.send(result);
                        ctx.request_repaint();
                    });
                }
                ui.hyperlink_to(
                    "GitHub releases",
                    "https://github.com/WoofahRayetCode/inFAMOUS-US/releases",
                );
                ui.hyperlink_to(
                    "Report an issue",
                    "https://github.com/WoofahRayetCode/inFAMOUS-US/issues/new",
                );
            });
            if self.update_check.is_some() {
                muted(ui, "Checking for updates...");
            }
            match &self.update_result {
                Some(Ok(info)) if info.available => {
                    ui.colored_label(AMBER, format!("Update available ({}). Open Setup or run Update inFamous.cmd to install it.", info.version));
                }
                Some(Ok(info)) => {
                    muted(ui, format!("Up to date ({})", info.version));
                }
                Some(Err(error)) => {
                    ui.colored_label(AMBER, error);
                }
                None => {}
            }
        });
        card(ui, |ui| {
            ui.label(RichText::new("Credits").size(19.0).strong());
            for (label, url) in CREDITS {
                ui.hyperlink_to(label, url);
            }
        });
    }
}

impl eframe::App for Dashboard {
    fn update(&mut self, ctx: &egui::Context, _: &mut eframe::Frame) {
        if let Some(features) = self.feature_check.as_ref().and_then(|r| r.try_recv().ok()) {
            self.state.set_features(features);
            self.feature_check = None;
        }
        if let Some(result) = self.update_check.as_ref().and_then(|r| r.try_recv().ok()) {
            self.update_result = Some(result);
            self.update_check = None;
        }
        egui::TopBottomPanel::top("tabs")
            .frame(
                egui::Frame::none()
                    .fill(PANEL)
                    .inner_margin(egui::Margin::symmetric(22.0, 10.0)),
            )
            .show(ctx, |ui| {
                if self.restore.is_some() {
                    ui.disable();
                }
                ui.horizontal_wrapped(|ui| {
                    ui.label(RichText::new("inFamous PC").strong().color(BLUE));
                    ui.separator();
                    for (tab, label) in [
                        (Tab::Play, "Play"),
                        (Tab::Settings, "Display & Controls"),
                        (Tab::Saves, "Saves"),
                        (Tab::Achievements, "Achievements"),
                        (Tab::Support, "Support"),
                    ] {
                        if ui.selectable_label(self.tab == tab, label).clicked() && self.tab != tab
                        {
                            self.tab = tab;
                            if tab == Tab::Saves {
                                self.refresh_saves();
                            }
                            if tab == Tab::Achievements {
                                self.refresh_achievements();
                            }
                        }
                    }
                });
            });
        egui::TopBottomPanel::bottom("actions")
            .frame(
                egui::Frame::none()
                    .fill(PANEL)
                    .inner_margin(egui::Margin::symmetric(22.0, 11.0)),
            )
            .show(ctx, |ui| {
                if self.restore.is_some() {
                    ui.disable();
                }
                if let Some((text, error)) = &self.notice {
                    ui.colored_label(if *error { AMBER } else { BLUE }, text);
                }
                ui.horizontal(|ui| {
                    muted(ui, self.summary());
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        let text = if self.state.launch.settings_only {
                            "Save & close"
                        } else {
                            "Play"
                        };
                        let play =
                            egui::Button::new(RichText::new(text).strong().color(BACKGROUND))
                                .fill(BLUE)
                                .min_size(egui::vec2(110.0, 33.0));
                        if ui.add_enabled(self.readiness().is_ok(), play).clicked() {
                            match self.state.play() {
                                Ok(()) => {
                                    self.played.store(true, Ordering::Relaxed);
                                    ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                                }
                                Err(e) => self.notify(Err(e)),
                            }
                        }
                        if ui
                            .add_enabled(self.state.dirty(), egui::Button::new("Save settings"))
                            .clicked()
                        {
                            let result = self.state.save().map(|()| "Settings saved.".into());
                            self.notify(result);
                        }
                    });
                });
            });
        egui::CentralPanel::default()
            .frame(
                egui::Frame::none()
                    .fill(BACKGROUND)
                    .inner_margin(egui::Margin::symmetric(22.0, 16.0)),
            )
            .show(ctx, |ui| {
                ui.add_enabled_ui(self.restore.is_none(), |ui| {
                    egui::ScrollArea::vertical()
                        .id_salt(match self.tab {
                            Tab::Play => "play",
                            Tab::Settings => "settings",
                            Tab::Saves => "saves",
                            Tab::Achievements => "achievements",
                            Tab::Support => "support",
                        })
                        .auto_shrink([false, false])
                        .show(ui, |ui| match self.tab {
                            Tab::Play => self.home(ui),
                            Tab::Settings => self.settings(ui),
                            Tab::Saves => self.saves(ui),
                            Tab::Achievements => self.achievements(ui),
                            Tab::Support => self.support(ui),
                        });
                });
            });
        if let Some((name, scope)) = self.restore.clone() {
            egui::Window::new("Restore backup")
                .anchor(egui::Align2::CENTER_CENTER, egui::Vec2::ZERO)
                .resizable(false)
                .collapsible(false)
                .show(ctx, |ui| {
                    ui.label(format!("{} from {name}", scope_name(scope)));
                    ui.label("A recovery backup will be created before files are replaced.");
                    ui.horizontal(|ui| {
                        if ui.button("Cancel").clicked() {
                            self.restore = None;
                        }
                        if ui
                            .add_enabled(!self.state.dirty(), egui::Button::new("Restore"))
                            .clicked()
                        {
                            self.restore = None;
                            self.restore_backup(&name, scope);
                        }
                    });
                });
        }
    }
}

fn banner_texture(ctx: &egui::Context) -> Option<egui::TextureHandle> {
    let png = include_bytes!("../../../assets/launcher-skyline.png");
    let picture = image::load_from_memory_with_format(png, image::ImageFormat::Png)
        .ok()?
        .into_rgb8();
    let size = [picture.width() as usize, picture.height() as usize];
    let color = egui::ColorImage::from_rgb(size, picture.as_raw());
    Some(ctx.load_texture("industrial waterfront", color, egui::TextureOptions::LINEAR))
}

pub(super) fn run(state: State) -> bool {
    let played = Arc::new(AtomicBool::new(false));
    let result = played.clone();
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1000.0, 660.0])
            .with_min_inner_size([760.0, 530.0])
            .with_icon(egui::IconData {
                rgba: icon_rgba(64),
                width: 64,
                height: 64,
            }),
        centered: true,
        ..Default::default()
    };
    if let Err(error) = eframe::run_native(
        "inFamous PC",
        options,
        Box::new(move |cc| Ok(Box::new(Dashboard::new(state, result, &cc.egui_ctx)))),
    ) {
        message(&format!("Could not open the launcher: {error}"));
    }
    played.load(Ordering::Relaxed)
}
