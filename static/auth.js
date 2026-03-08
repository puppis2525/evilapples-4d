async function loadAuthUser() {
    try {
        const resp = await fetch('/api/auth/user');
        const data = await resp.json();
        return data;
    } catch (e) {
        return { logged_in: false };
    }
}

function renderAuthButton(user) {
    const container = document.getElementById('auth-btn');
    if (!container) return;

    if (user.logged_in) {
        const avatar = user.avatar_url
            ? `<img src="${user.avatar_url}" alt="" class="w-7 h-7 rounded-full border border-evil-500/50">`
            : `<div class="w-7 h-7 rounded-full bg-evil-500 flex items-center justify-center text-white text-xs font-bold">${(user.global_name || user.username || '?')[0].toUpperCase()}</div>`;

        container.innerHTML = `
            <div class="flex items-center gap-2 relative group cursor-pointer">
                ${avatar}
                <span class="text-evil-200 text-sm font-semibold hidden sm:inline">${user.global_name || user.username}</span>
                <div class="absolute top-full right-0 mt-2 bg-evil-surface border border-evil-700/60 rounded-xl shadow-xl py-2 px-1 hidden group-hover:block min-w-[140px] z-50">
                    <div class="px-3 py-1.5 text-evil-400 text-xs">ID: ${user.discord_id}</div>
                    <a href="/auth/discord/logout" class="block px-3 py-1.5 text-sm text-red-400 hover:bg-evil-card rounded-lg transition">Sign Out</a>
                </div>
            </div>`;
    } else {
        container.innerHTML = `
            <a href="/auth/discord/login" class="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-[#5865F2] hover:bg-[#4752C4] text-white text-sm font-semibold transition">
                <svg width="16" height="12" viewBox="0 0 71 55" fill="currentColor"><path d="M60.1 4.9A58.5 58.5 0 0045.4.2a.2.2 0 00-.2.1 40.8 40.8 0 00-1.8 3.7 54 54 0 00-16.2 0A37.3 37.3 0 0025.4.3a.2.2 0 00-.2-.1A58.4 58.4 0 0010.5 5a.2.2 0 00-.1 0A59.7 59.7 0 00.3 45.3a.2.2 0 000 .2 58.8 58.8 0 0017.7 9a.2.2 0 00.3-.1 42 42 0 003.6-5.9.2.2 0 00-.1-.3 38.8 38.8 0 01-5.5-2.6.2.2 0 01 0-.4l1.1-.9a.2.2 0 01.2 0 42 42 0 0035.6 0 .2.2 0 01.2 0l1.1.9a.2.2 0 010 .3 36.4 36.4 0 01-5.5 2.7.2.2 0 00-.1.3 47.2 47.2 0 003.6 5.8.2.2 0 00.3.1 58.6 58.6 0 0017.7-9 .2.2 0 000-.1A59.2 59.2 0 0060.2 5a.2.2 0 00-.1 0zM23.7 37.3c-3.5 0-6.4-3.2-6.4-7.1s2.8-7.1 6.4-7.1 6.5 3.2 6.4 7.1c0 3.9-2.8 7.1-6.4 7.1zm23.6 0c-3.5 0-6.4-3.2-6.4-7.1s2.8-7.1 6.4-7.1 6.5 3.2 6.4 7.1c0 3.9-2.8 7.1-6.4 7.1z"/></svg>
                Sign In
            </a>`;
    }
}

function getAuthDiscordId(user) {
    return user && user.logged_in ? user.discord_id : '';
}

async function initAuth() {
    const user = await loadAuthUser();
    renderAuthButton(user);
    return user;
}
