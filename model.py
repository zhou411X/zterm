"""Saved aliases and appearance, with static shell SSH alias resolution."""

import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def valid_alias(alias):
    return bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", alias))


def parse_ssh_alias(command):
    """Read a direct SSH invocation, without evaluating shell syntax."""
    if "$" in command or "`" in command:
        return None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        args = list(lexer)
    except ValueError:
        return None
    if not args or args[0] not in ("ssh", "/usr/bin/ssh"):
        return None
    options, user, host = [], None, None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in ("-4", "-6", "-C"):
            options.append(arg)
        elif arg[:2] in ("-l", "-p", "-i", "-J", "-o"):
            option = arg[:2]
            value = arg[2:]
            if not value:
                index += 1
                if index == len(args):
                    return None
                value = args[index]
            if option == "-l":
                user = value
            elif option == "-o":
                pair = re.split(r"[=\s]+", value, maxsplit=1)
                if len(pair) != 2:
                    return None
                key, value = pair
                key = key.lower()
                if key == "user":
                    user = value
                elif key == "hostname":
                    host = value
                elif key in ("serveraliveinterval", "serveralivecountmax", "connecttimeout",
                             "port", "identityfile", "identitiesonly", "proxyjump"):
                    options.extend(["-o", key + "=" + value])
                else:
                    return None
            else:
                options.extend([option, str(Path(value).expanduser()) if option == "-i" else value])
        else:
            if index != len(args) - 1:
                return None
            login, separator, destination = arg.rpartition("@")
            if separator:
                user = login
            host = host or destination
            if not valid_alias(host) or (user is not None and not valid_alias(user)):
                return None
            return {"host": host, "user": user, "options": options}
        index += 1
    return None


def bash_ssh_aliases(path=None):
    path = Path(path) if path is not None else Path.home() / ".bashrc"
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return {}
    aliases = {}
    for line in lines:
        try:
            parts = shlex.split(line, comments=True)
        except ValueError:
            continue
        if not parts or parts[0] != "alias":
            continue
        for definition in parts[1:]:
            name, separator, command = definition.partition("=")
            if separator and valid_alias(name):
                # A later redefinition supersedes an earlier supported SSH alias.
                aliases.pop(name, None)
                target = parse_ssh_alias(command)
                if target is not None:
                    aliases[name] = target
    return aliases


def validate_server(profile):
    if (not isinstance(profile, dict)
            or not {"name", "host", "username", "port"}.issubset(profile)
            or set(profile) - {"name", "host", "username", "port", "password_saved", "credential_id"}
            or not isinstance(profile["name"], str) or not 1 <= len(profile["name"].strip()) <= 80
            or any(ord(char) < 32 or ord(char) == 127 for char in profile["name"])
            or not isinstance(profile["host"], str)
            or not isinstance(profile["username"], str) or not valid_alias(profile["username"])
            or type(profile["port"]) is not int or not 1 <= profile["port"] <= 65535
            or type(profile.get("password_saved", False)) is not bool):
        raise ValueError("服务器信息无效：请填写别名、IP、用户名和 1–65535 的端口。")
    if "credential_id" in profile and (not profile.get("password_saved")
            or not isinstance(profile["credential_id"], str)
            or not valid_alias(profile["credential_id"])):
        raise ValueError("密码引用无效。")
    try:
        ipaddress.ip_address(profile["host"])
    except ValueError:
        raise ValueError("请输入有效的 IPv4 或 IPv6 地址。") from None
    return dict(profile)


class AliasStore:
    def __init__(self, path=None):
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.path = Path(path) if path else root / "ssh-workbench/servers.json"
        self.profiles = {}

    def load(self):
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text())
        aliases = data.get("aliases") if isinstance(data, dict) else None
        if (not isinstance(aliases, list)
                or any(not isinstance(a, str) or not valid_alias(a) for a in aliases)
                or len(set(aliases)) != len(aliases)):
            raise ValueError("别名配置格式错误；请检查 " + str(self.path))
        profiles = data.get("servers", {})
        if not isinstance(profiles, dict) or set(profiles) - set(aliases):
            raise ValueError("服务器配置格式错误；请检查 " + str(self.path))
        self.profiles = {key: validate_server(profile) for key, profile in profiles.items()}
        return aliases

    def save(self, aliases, profiles=None):
        profiles = self.profiles if profiles is None else profiles
        profiles = {key: validate_server(profiles[key]) for key in aliases if key in profiles}
        data = {"aliases": aliases}
        if profiles:
            data["servers"] = profiles
        save_json(self.path, data)
        self.profiles = profiles


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".settings-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# GNOME Terminal 3.52 schema defaults as shipped in Ubuntu 24.04.
GNOME_PALETTE = ["#171421", "#c01c28", "#26a269", "#a2734c", "#12488b", "#a347ba", "#2aa1b3", "#d0cfcc",
                 "#5e5c64", "#f66151", "#33da7a", "#e9ad0c", "#2a7bde", "#c061cb", "#33c7de", "#ffffff"]

# Removed presets are only recognized for migration, never offered in the UI.
LEGACY_THEME_MIGRATIONS = {
    "classic": ("ubuntu-default", ("#eeeeec", "#241f31", "#eeeeec")),
    "dark": ("gnome-gray-on-black", ("#e5e7eb", "#15181e", "#93c5fd")),
    "light": ("gnome-black-on-white", ("#24292f", "#fafafa", "#0969da")),
    "ocean": ("ubuntu-default", ("#d8e7f3", "#102332", "#7dd3fc")),
}

# RGB sources and ANSI adaptation details are recorded in THEME_SOURCES.txt.
# Solarized follows the local Vim variant, including its experimental green.
# Gruvbox/Peaksea use syntax highlight accents in ANSI 1–6 and 9–14.
# PaperColor additionally adapts the default Vim slots seen in the user's
# screenshots; a terminal palette cannot distinguish syntax groups sharing a slot.
THEMES = {
    **{key: {"name": name, "foreground": fg, "background": bg, "cursor": fg,
             "palette": GNOME_PALETTE.copy()}
       for key, name, fg, bg in (
           ("gnome-black-on-light-yellow", "浅黄背景黑字", "#000000", "#ffffdd"),
           ("gnome-black-on-white", "白底黑字", "#000000", "#ffffff"),
           ("gnome-gray-on-black", "黑底灰字", "#aaaaaa", "#000000"),
           ("gnome-white-on-black", "黑底白字", "#ffffff", "#000000"),
           ("gnome-green-on-black", "黑底绿字", "#00ff00", "#000000"),
           ("ubuntu-default", "Ubuntu 默认", "#ffffff", "#300a24"),
       )},
    "solarized-light": {
        "name": "Solarized Light", "foreground": "#657b83", "background": "#fdf6e3", "cursor": "#657b83",
        "palette": ["#eee8d5", "#dc322f", "#719e07", "#b58900", "#268bd2", "#d33682", "#2aa198", "#073642",
                    "#fdf6e3", "#cb4b16", "#93a1a1", "#839496", "#657b83", "#6c71c4", "#586e75", "#002b36"],
    },
    "solarized-dark": {
        "name": "Solarized Dark", "foreground": "#839496", "background": "#002b36", "cursor": "#839496",
        "palette": ["#073642", "#dc322f", "#719e07", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                    "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"],
    },
    "gruvbox-light": {
        "name": "Gruvbox Light", "foreground": "#3c3836", "background": "#fbf1c7", "cursor": "#3c3836",
        "palette": ["#fbf1c7", "#9d0006", "#79740e", "#b57614", "#076678", "#8f3f71", "#427b58", "#7c6f64",
                    "#928374", "#9d0006", "#79740e", "#b57614", "#076678", "#8f3f71", "#427b58", "#3c3836"],
    },
    "gruvbox-dark": {
        "name": "Gruvbox Dark", "foreground": "#ebdbb2", "background": "#282828", "cursor": "#ebdbb2",
        "palette": ["#282828", "#fb4934", "#b8bb26", "#fabd2f", "#83a598", "#d3869b", "#8ec07c", "#a89984",
                    "#928374", "#fb4934", "#b8bb26", "#fabd2f", "#83a598", "#d3869b", "#8ec07c", "#ebdbb2"],
    },
    "papercolor-light": {
        "name": "PaperColor Light", "foreground": "#444444", "background": "#eeeeee", "cursor": "#005f87",
        "palette": ["#eeeeee", "#af0000", "#d70087", "#d70087", "#878787", "#8700af", "#005f87", "#444444",
                    "#878787", "#af0000", "#d70087", "#d70087", "#878787", "#8700af", "#005f87", "#444444"],
        "indexed_colors": {81: "#005faf", 121: "#d70087"},
        "accent": "#005faf",
    },
    "papercolor-dark": {
        "name": "PaperColor Dark", "foreground": "#d0d0d0", "background": "#1c1c1c", "cursor": "#c6c6c6",
        "palette": ["#1c1c1c", "#af005f", "#d7af5f", "#afd700", "#00afaf", "#af87d7", "#808080", "#d0d0d0",
                    "#808080", "#af005f", "#d7af5f", "#afd700", "#00afaf", "#af87d7", "#808080", "#d0d0d0"],
        "indexed_colors": {81: "#00afaf", 121: "#afd700"},
    },
    "peaksea-light": {
        "name": "Peaksea Light", "foreground": "#000000", "background": "#e0e0e0", "cursor": "#008000",
        "palette": ["#e0e0e0", "#c03000", "#009030", "#907000", "#2060a8", "#a030a0", "#007068", "#000000",
                    "#606000", "#c03000", "#009030", "#907000", "#2060a8", "#a030a0", "#007068", "#000000"],
    },
    "peaksea-dark": {
        "name": "Peaksea Dark", "foreground": "#d0d0d0", "background": "#202020", "cursor": "#00f000",
        "palette": ["#202020", "#f08060", "#60f080", "#e0c060", "#c0d8f8", "#f0c0f0", "#80c0e0", "#d0d0d0",
                    "#d0d090", "#f08060", "#60f080", "#e0c060", "#c0d8f8", "#f0c0f0", "#80c0e0", "#d0d0d0"],
    },
    # Local Vim RGB snapshots; see THEME_SOURCES.txt for the ANSI mapping.
    "monokai": {
        "name": "Monokai", "foreground": "#e8e8e3", "background": "#272822", "cursor": "#e8e8e3",
        "palette": ["#272822", "#e73c50", "#a6e22d", "#e6db74", "#66d9ef", "#ae81ff", "#a1efe4", "#e8e8e3",
                    "#64645e", "#f92772", "#9ec400", "#e7c547", "#7aa6da", "#b77ee0", "#54ced6", "#ffffff"],
    },
    "ayu-light": {
        "name": "Ayu Light", "foreground": "#5c6166", "background": "#fcfcfc", "cursor": "#f29718",
        "palette": ["#fcfcfc", "#e65050", "#86b300", "#a37acc", "#eba400", "#fa8532", "#4cbf99", "#5c6166",
                    "#adaeb1", "#e65050", "#86b300", "#a37acc", "#eba400", "#fa8532", "#4cbf99", "#5c6166"],
    },
    "ayu-dark": {
        "name": "Ayu Dark", "foreground": "#bfbdb6", "background": "#10141c", "cursor": "#e6b450",
        "palette": ["#10141c", "#d95757", "#aad94c", "#d2a6ff", "#ffb454", "#ff8f40", "#95e6cb", "#bfbdb6",
                    "#5a6673", "#d95757", "#aad94c", "#d2a6ff", "#ffb454", "#ff8f40", "#95e6cb", "#bfbdb6"],
    },
    "ayu-mirage": {
        "name": "AyuMirage", "foreground": "#cccac2", "background": "#242936", "cursor": "#ffcc66",
        "palette": ["#242936", "#ff6666", "#d5ff80", "#dfbfff", "#ffcd66", "#ffa659", "#95e6cb", "#cccac2",
                    "#6e7c8f", "#ff6666", "#d5ff80", "#dfbfff", "#ffcd66", "#ffa659", "#95e6cb", "#cccac2"],
    },
    "breadog": {
        "name": "Breadog", "foreground": "#362c24", "background": "#f1ebe6", "cursor": "#362c24",
        "palette": ["#f1ebe6", "#b10b00", "#007232", "#9a5500", "#005cb4", "#9b0097", "#006a78", "#362c24",
                    "#736354", "#b10b00", "#007232", "#9a5500", "#005cb4", "#9b0097", "#006a78", "#362c24"],
    },
    "clawbeige-light": {
        "name": "ClawBeige Light", "foreground": "#2c2420", "background": "#f8f8f6", "cursor": "#c15f3c",
        "palette": ["#f8f8f6", "#c13c3c", "#7a5a20", "#a04a20", "#6a4a90", "#c15f3c", "#c15f3c", "#2c2420",
                    "#a89888", "#c13c3c", "#7a5a20", "#a04a20", "#6a4a90", "#c15f3c", "#c15f3c", "#2c2420"],
    },
    "clawbeige-dark": {
        "name": "ClawBeige Dark", "foreground": "#e8e6e3", "background": "#2c2620", "cursor": "#c15f3c",
        "palette": ["#2c2620", "#ff6b6b", "#d4a96a", "#e8a87c", "#d4b483", "#c15f3c", "#e8a87c", "#e8e6e3",
                    "#6a6158", "#ff6b6b", "#d4a96a", "#e8a87c", "#d4b483", "#c15f3c", "#e8a87c", "#e8e6e3"],
    },
    "dragonbeige": {
        "name": "DragonBeige", "foreground": "#4a3b32", "background": "#f5f0e6", "cursor": "#8b5a2b",
        "palette": ["#f5f0e6", "#0501ff", "#5a7247", "#7a4c65", "#94422e", "#9c5d27", "#b35900", "#4a3b32",
                    "#9a8c7e", "#0501ff", "#5a7247", "#7a4c65", "#94422e", "#9c5d27", "#b35900", "#4a3b32"],
    },
    "faulty-schematics": {
        "name": "FaultySchematics", "foreground": "#262013", "background": "#dcdad7", "cursor": "#ff4f00",
        "palette": ["#dcdad7", "#b51f1f", "#7f7f91", "#9a8f7e", "#6d5c39", "#8c7649", "#ff4f00", "#262013",
                    "#61616e", "#b51f1f", "#7f7f91", "#9a8f7e", "#6d5c39", "#8c7649", "#ff4f00", "#262013"],
    },
    "nightowl-light": {
        "name": "NightOwl Light", "foreground": "#403f53", "background": "#fbfbfb", "cursor": "#90a7b2",
        "palette": ["#fbfbfb", "#ff2c83", "#4876d6", "#4876d6", "#4876d6", "#994cc3", "#aa0982", "#403f53",
                    "#989fb1", "#ff2c83", "#4876d6", "#4876d6", "#4876d6", "#994cc3", "#aa0982", "#403f53"],
    },
    "nightowl-dark": {
        "name": "NightOwl Dark", "foreground": "#d6deeb", "background": "#011627", "cursor": "#80a4c2",
        "palette": ["#011627", "#ffffff", "#ecc48d", "#82aaff", "#82aaff", "#c792ea", "#f78c6c", "#d6deeb",
                    "#637777", "#ffffff", "#ecc48d", "#82aaff", "#82aaff", "#c792ea", "#f78c6c", "#d6deeb"],
    },
    "retrotheme": {
        "name": "RetroTheme", "foreground": "#ffffff", "background": "#141212", "cursor": "#ffffff",
        "palette": ["#141212", "#f8f8f0", "#fefa44", "#d63956", "#00cc74", "#25bffb", "#71328c", "#ffffff",
                    "#535654", "#f8f8f0", "#fefa44", "#d63956", "#00cc74", "#25bffb", "#71328c", "#ffffff"],
    },
    "tokyonight-light": {
        "name": "TokyoNight Light", "foreground": "#343b59", "background": "#e6e7ed", "cursor": "#363c4d",
        "palette": ["#e6e7ed", "#942f2f", "#385f0d", "#965027", "#2959aa", "#65359d", "#363c4d", "#343b59",
                    "#888b94", "#942f2f", "#385f0d", "#965027", "#2959aa", "#65359d", "#363c4d", "#343b59"],
    },
    "tokyonight-dark": {
        "name": "TokyoNight Dark", "foreground": "#a9b1d6", "background": "#1a1b26", "cursor": "#c0caf5",
        "palette": ["#1a1b26", "#ff5370", "#9ece6a", "#ff9e64", "#7aa2f7", "#bb9af7", "#89ddff", "#a9b1d6",
                    "#51597d", "#ff5370", "#9ece6a", "#ff9e64", "#7aa2f7", "#bb9af7", "#89ddff", "#a9b1d6"],
    },
    "tokyonight-storm": {
        "name": "TokyoNight Storm", "foreground": "#a9b1d6", "background": "#24283b", "cursor": "#c0caf5",
        "palette": ["#24283b", "#ff5370", "#9ece6a", "#ff9e64", "#7aa2f7", "#bb9af7", "#89ddff", "#a9b1d6",
                    "#5f6996", "#ff5370", "#9ece6a", "#ff9e64", "#7aa2f7", "#bb9af7", "#89ddff", "#a9b1d6"],
    },
    "vintage-light": {
        "name": "VintageLight", "foreground": "#000000", "background": "#ffffff", "cursor": "#316ac5",
        "palette": ["#ffffff", "#cd3131", "#a31515", "#0000ff", "#000000", "#0000ff", "#0000ff", "#000000",
                    "#666666", "#cd3131", "#a31515", "#0000ff", "#000000", "#0000ff", "#0000ff", "#000000"],
    },
}


INTERFACE_THEMES = {"follow-terminal": "跟随终端配色（推荐）", "system": "跟随系统外观",
                    **{key: theme["name"] for key, theme in THEMES.items()}}


def blend_colors(background, foreground, amount):
    return "#" + "".join(f"{round(bg + (fg - bg) * amount):02x}"
                         for bg, fg in zip(bytes.fromhex(background[1:]), bytes.fromhex(foreground[1:])))


def terminal_palette(name):
    # Always provide all 256 entries so switching away from PaperColor restores
    # the standard xterm cube and gray ramp, including indices 81 and 121.
    theme = THEMES[name]
    levels = (0, 95, 135, 175, 215, 255)
    palette = theme["palette"] + [f"#{red:02x}{green:02x}{blue:02x}"
                                  for red in levels for green in levels for blue in levels]
    palette += [f"#{value:02x}{value:02x}{value:02x}" for value in range(8, 239, 10)]
    for index, color in theme.get("indexed_colors", {}).items():
        palette[index] = color
    return palette


def interface_colors(settings):
    name = settings.get("interface_theme", "follow-terminal")
    if name == "system":
        return None
    if name == "follow-terminal":
        name = settings["theme"]
    theme = THEMES[name]
    background = theme["background"]
    foreground = "#d0d0d0" if name == "gnome-green-on-black" else theme["foreground"]
    accent = ("#e95420" if name == "ubuntu-default" else
              "#00af00" if name == "gnome-green-on-black" else
              theme.get("accent", theme["palette"][4]))
    return {"background": background, "foreground": foreground, "accent": accent,
            "surface": blend_colors(background, foreground, 0.06),
            "hover": blend_colors(background, foreground, 0.12),
            "border": blend_colors(background, foreground, 0.25),
            "selection": blend_colors(background, accent, 0.25)}


BACKGROUND_DEFAULTS = {"background_opacity": 1.0, "background_image": "",
                       "background_image_opacity": 0.25, "background_blur": 0,
                       "background_on_terminal": False}


def default_appearance():
    return {"font": "Monospace 11", "theme": "ubuntu-default", "interface_theme": "follow-terminal",
            **BACKGROUND_DEFAULTS,
            **{key: THEMES["ubuntu-default"][key] for key in ("foreground", "background", "cursor")}}


class AppearanceStore:
    def __init__(self, path=None):
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.path = Path(path) if path else root / "ssh-workbench/appearance.json"

    def validate(self, data):
        if (not isinstance(data, dict)
                or not isinstance(data.get("font"), str) or not data["font"].strip()
                or not isinstance(data.get("theme"), str)
                or data["theme"] not in THEMES.keys() | LEGACY_THEME_MIGRATIONS.keys()
                or not isinstance(data.get("interface_theme", "follow-terminal"), str)
                or data.get("interface_theme", "follow-terminal") not in INTERFACE_THEMES
                or any(not isinstance(data.get(key), str)
                       or not re.fullmatch(r"#[0-9a-fA-F]{6}", data[key])
                       for key in ("foreground", "background", "cursor"))):
            raise ValueError("外观配置格式错误；请检查 " + str(self.path))
        effects = {key: data.get(key, default) for key, default in BACKGROUND_DEFAULTS.items()}
        for key, low, high in (("background_opacity", 0.15, 1),
                               ("background_image_opacity", 0, 1), ("background_blur", 0, 40)):
            value = effects[key]
            if type(value) not in (int, float) or not low <= value <= high:
                raise ValueError("无效的背景设置：" + key)
        if (not isinstance(effects["background_image"], str)
                or "\0" in effects["background_image"]
                or (effects["background_image"] and not Path(effects["background_image"]).is_absolute())
                or type(effects["background_on_terminal"]) is not bool):
            raise ValueError("无效的背景图片设置")
        if data["theme"] in LEGACY_THEME_MIGRATIONS:
            theme, previous_colors = LEGACY_THEME_MIGRATIONS[data["theme"]]
            data = dict(data, theme=theme)
            for key, previous in zip(("foreground", "background", "cursor"), previous_colors):
                if data[key].lower() == previous:
                    data[key] = THEMES[theme][key]
        return dict(data, **effects, interface_theme=data.get("interface_theme", "follow-terminal"))

    def load(self):
        if not self.path.exists():
            return default_appearance()
        return self.validate(json.loads(self.path.read_text()))

    def save(self, data):
        save_json(self.path, self.validate(data))


# Keep idle SSH connections alive (equivalent to `ssh -o ServerAliveInterval=60`),
# so NAT timeouts and firewalls do not silently drop the session.
SSH_KEEPALIVE = ["-o", "ServerAliveInterval=60", "-o", "ServerAliveCountMax=3"]


def ssh_command(alias, bashrc=None):
    if not valid_alias(alias):
        raise ValueError("无效的 SSH 别名")
    aliases = bash_ssh_aliases(bashrc)
    target = aliases.get(alias)
    if target is None:
        target = next((value for value in aliases.values() if value["host"] == alias), None)
    command = ["/usr/bin/ssh"]
    if target is not None:
        if target["user"]:
            command.extend(["-l", target["user"]])
        # Retain the original alias for SSH Host rules while resolving its target.
        command.extend(["-o", "HostName=" + target["host"], *target["options"]])
    # Keep the connection alive; these defaults come after any explicit
    # ServerAlive* option from a .bashrc alias, which OpenSSH honours first.
    command.extend(SSH_KEEPALIVE)
    return command + [alias]


def server_ssh_command(profile):
    profile = validate_server(profile)
    command = ["/usr/bin/ssh", "-l", profile["username"], "-p", str(profile["port"]),
               "-o", "HostName=" + profile["host"], *SSH_KEEPALIVE]
    if profile.get("password_saved"):
        command += ["-oBatchMode=no", "-oPreferredAuthentications=publickey,password",
                    "-oNumberOfPasswordPrompts=1"]
    return command + [profile["host"]]


def legacy_server_profile(alias):
    """Resolve existing SSH options locally without starting a connection."""
    command = ssh_command(alias)
    result = subprocess.run(command[:1] + ["-G"] + command[1:],
                            capture_output=True, text=True, check=True, timeout=5)
    options = dict(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)
    return {"name": alias, "host": options["hostname"], "username": options["user"],
            "port": int(options["port"]), "password_saved": False}
