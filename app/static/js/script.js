document.addEventListener('DOMContentLoaded', () => {
    loadHistory();
    setupThemeToggle();
    setupTypingIndicator();
    setupSettingsMenu();
    loadPreferences();
    setupEmojiPicker();
    setupPlaceholderAnimation();
    setupInputEvents();
    setupFileUpload();
    setupSocketIO();
    checkTasks();
    checkAchievements();
});

const socket = io();

function showNotification(message, type = 'info') {
    Toastify({
        text: message,
        duration: 3000,
        gravity: "top",
        position: "right",
        backgroundColor: type === 'error' ? "#ef4444" : type === 'success' ? "#10b981" : type === 'achievement' ? "#f59e0b" : "#3b82f6",
        stopOnFocus: true,
        className: 'animate-slideIn',
    }).showToast();
}

function updateStatus(status) {
    const statusDot = document.querySelector('.status-dot');
    const statusText = document.querySelector('#statusIndicator span');
    if (status === 'processing') {
        statusDot.classList.add('processing');
        statusText.textContent = 'Procesando...';
    } else {
        statusDot.classList.remove('processing');
        statusText.textContent = 'En línea';
    }
}

function setupThemeToggle() {
    const themeToggle = document.getElementById('themeToggle');
    const body = document.body;
    const isLight = localStorage.getItem('theme') === 'light';
    if (isLight) body.classList.add('light');
    themeToggle.innerHTML = isLight ? 
        '<svg class="w-5 h-5 text-gray-800" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"></path></svg>' :
        '<svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>';
    themeToggle.addEventListener('click', () => {
        body.classList.toggle('light');
        localStorage.setItem('theme', body.classList.contains('light') ? 'light' : 'dark');
        themeToggle.innerHTML = body.classList.contains('light') ? 
            '<svg class="w-5 h-5 text-gray-800" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"></path></svg>' :
            '<svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>';
        const emojiPicker = document.getElementById('emojiPicker');
        if (emojiPicker.firstChild) {
            emojiPicker.firstChild.data.theme = body.classList.contains('light') ? 'light' : 'dark';
        }
    });
}

function setupTypingIndicator() {
    const userInput = document.getElementById('userInput');
    const chatBox = document.getElementById('chatBox');
    let typingTimeout;
    userInput.addEventListener('input', () => {
        clearTimeout(typingTimeout);
        if (!document.querySelector('.user-typing')) {
            const typingIndicator = document.createElement('div');
            typingIndicator.className = 'typing-indicator user-typing';
            typingIndicator.innerHTML = 'Usuario escribiendo... <span class="dot"></span><span class="dot"></span><span class="dot"></span>';
            chatBox.appendChild(typingIndicator);
            chatBox.scrollTop = chatBox.scrollHeight;
        }
        typingTimeout = setTimeout(() => {
            const typingIndicator = document.querySelector('.user-typing');
            if (typingIndicator) typingIndicator.remove();
        }, 2000);
    });
}

function setupSettingsMenu() {
    const settingsButton = document.getElementById('settingsButton');
    const settingsModal = document.getElementById('settingsModal');
    const closeSettings = document.getElementById('closeSettings');
    const settingsBackdrop = document.getElementById('settingsBackdrop');
    const settingsPopup = document.querySelector('.settings-popup');

    settingsButton.addEventListener('click', () => {
        settingsModal.classList.remove('hidden');
    });

    closeSettings.addEventListener('click', () => {
        settingsModal.classList.add('hidden');
    });

    settingsBackdrop.addEventListener('click', () => {
        settingsModal.classList.add('hidden');
    });

    settingsPopup.addEventListener('click', (e) => {
        e.stopPropagation();
    });

    settingsPopup.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            e.stopPropagation();
            savePreferences();
        } else if (e.key === 'Escape') {
            settingsModal.classList.add('hidden');
        }
    });
}

async function loadPreferences() {
    try {
        const response = await fetch('/preferences');
        const data = await response.json();
        if (response.ok) {
            document.getElementById('modelSelect').value = data.model;
            document.getElementById('toneSelect').value = data.tone;
            document.getElementById('languageSelect').value = data.language || 'auto';
            document.getElementById('bio').value = data.bio || '';
            document.getElementById('userAvatar').src = data.avatar || '/static/uploads/default.png';
        }
    } catch (error) {
        showNotification('Error al cargar preferencias: ' + error.message, 'error');
    }
}

async function savePreferences() {
    const model = document.getElementById('modelSelect').value;
    const tone = document.getElementById('toneSelect').value;
    const language = document.getElementById('languageSelect').value;
    const bio = document.getElementById('bio').value;
    const profilePicture = document.getElementById('profilePicture').files[0];

    const formData = new FormData();
    formData.append('model', model);
    formData.append('tone', tone);
    formData.append('language', language);
    formData.append('bio', bio);
    if (profilePicture) formData.append('profile_picture', profilePicture);

    try {
        const response = await fetch('/preferences', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();
        if (response.ok) {
            showNotification('Preferencias guardadas', 'success');
            document.getElementById('settingsModal').classList.add('hidden');
            if (data.avatar) document.getElementById('userAvatar').src = data.avatar;
        } else {
            showNotification(data.error || 'Error al guardar preferencias', 'error');
        }
    } catch (error) {
        showNotification('Error de conexión: ' + error.message, 'error');
    }
}

async function loadHistory() {
    const chatBox = document.getElementById('chatBox');
    try {
        const response = await fetch('/history');
        const messages = await response.json();
        if (response.ok) {
            let lastDate = null;
            messages.forEach(msg => {
                const messageDate = new Date(msg.timestamp).toLocaleDateString();
                if (messageDate !== lastDate) {
                    const separator = document.createElement('div');
                    separator.className = 'date-separator';
                    separator.textContent = messageDate;
                    chatBox.appendChild(separator);
                    lastDate = messageDate;
                }
                const userMessage = document.createElement('div');
                userMessage.className = 'message user-message';
                userMessage.dataset.messageId = msg.id;
                userMessage.innerHTML = `
                    <div class="message-content">${msg.user_message}${msg.file_url ? `<br><a href="${msg.file_url}" target="_blank">Archivo: ${msg.file_name}</a>` : ''}${msg.edited ? '<div class="edited-label">Editado</div>' : ''}</div>
                    <div class="avatar"><img src="${msg.avatar || '/static/uploads/default.png'}" alt="Avatar"></div>
                    <div class="message-timestamp">${msg.timestamp}</div>
                    <div class="edit-button" onclick="editMessage(${msg.id}, this)">✏️</div>
                    <div class="delete-button" onclick="deleteMessage(${msg.id}, this)">🗑️</div>
                `;
                chatBox.appendChild(userMessage);
                const aiMessage = document.createElement('div');
                aiMessage.className = 'message ai-message';
                aiMessage.innerHTML = `
                    <div class="avatar">IA</div>
                    <div class="message-content">${marked.parse(msg.ai_response)}</div>
                    <div class="message-timestamp">${msg.timestamp}</div>
                `;
                chatBox.appendChild(aiMessage);
            });
            chatBox.scrollTop = chatBox.scrollHeight;
        }
    } catch (error) {
        showNotification('Error al cargar historial: ' + error.message, 'error');
    }
}

async function editMessage(messageId, button) {
    const messageDiv = button.parentElement;
    const contentDiv = messageDiv.querySelector('.message-content');
    const currentText = contentDiv.firstChild.textContent;
    contentDiv.innerHTML = `
        <input type="text" class="edit-input" value="${currentText}">
        <button onclick="saveEdit(${messageId}, this)" class="bg-green-600 text-white px-2 py-1 rounded ml-2">Guardar</button>
        <button onclick="cancelEdit(this, '${currentText.replace(/'/g, "\\'")}')" class="bg-gray-600 text-white px-2 py-1 rounded ml-2">Cancelar</button>
    `;
    document.getElementById('userInput').placeholder = 'Edita tu mensaje...';
}

async function saveEdit(messageId, button) {
    const input = button.parentElement.querySelector('.edit-input');
    const newMessage = input.value.trim();
    if (!newMessage) {
        showNotification('El mensaje no puede estar vacío', 'error');
        return;
    }
    try {
        const response = await fetch('/edit_message', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message_id: messageId, new_message: newMessage })
        });
        const data = await response.json();
        if (response.ok) {
            const messageDiv = button.closest('.user-message');
            const contentDiv = messageDiv.querySelector('.message-content');
            contentDiv.innerHTML = `${newMessage}<div class="edited-label">Editado</div>`;
            document.getElementById('userInput').placeholder = 'Escribe tu mensaje...';
            showNotification('Mensaje editado', 'success');
        } else {
            showNotification(data.error || 'Error al editar el mensaje', 'error');
        }
    } catch (error) {
        showNotification('Error de conexión: ' + error.message, 'error');
    }
}

function cancelEdit(button, originalText) {
    const contentDiv = button.parentElement;
    contentDiv.innerHTML = originalText + (originalText.includes('Editado') ? '<div class="edited-label">Editado</div>' : '');
    document.getElementById('userInput').placeholder = 'Escribe tu mensaje...';
}

async function deleteMessage(messageId, button) {
    if (!confirm('¿Estás seguro de que quieres eliminar este mensaje?')) return;
    try {
        const response = await fetch('/delete_message', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message_id: messageId })
        });
        const data = await response.json();
        if (response.ok) {
            button.closest('.user-message').remove();
            showNotification('Mensaje eliminado', 'success');
        } else {
            showNotification(data.error || 'Error al eliminar el mensaje', 'error');
        }
    } catch (error) {
        showNotification('Error de conexión: ' + error.message, 'error');
    }
}

function showProgressBar() {
    const progressBar = document.getElementById('progressBar');
    progressBar.classList.remove('hidden');
    let progress = 0;
    const interval = setInterval(() => {
        progress += 10;
        progressBar.style.width = `${progress}%`;
        if (progress >= 100) {
            clearInterval(interval);
            progressBar.classList.add('hidden');
            progressBar.style.width = '0%';
        }
    }, 200);
    return () => clearInterval(interval);
}

async function sendMessage(message = null, file = null) {
    const userInput = document.getElementById('userInput');
    const chatBox = document.getElementById('chatBox');
    const quickReplies = document.getElementById('quickReplies');
    const inputContainer = document.querySelector('.input-container');
    const inputMessage = message || userInput.value.trim();

    if (!inputMessage && !file) return;

    inputContainer.classList.add('animate-bounce');
    setTimeout(() => inputContainer.classList.remove('animate-bounce'), 300);

    const messageDate = new Date().toLocaleDateString();
    const lastSeparator = chatBox.querySelector('.date-separator:last-child');
    if (!lastSeparator || lastSeparator.textContent !== messageDate) {
        const separator = document.createElement('div');
        separator.className = 'date-separator';
        separator.textContent = messageDate;
        chatBox.appendChild(separator);
    }
    const userMessage = document.createElement('div');
    userMessage.className = 'message user-message';
    userMessage.innerHTML = `
        <div class="message-content">${inputMessage}${file ? `<br><a href="#" class="file-link" data-file-name="${file.name}">Archivo: ${file.name}</a>` : ''}</div>
        <div class="avatar"><img src="${document.getElementById('userAvatar').src}" alt="Avatar"></div>
        <div class="message-timestamp">${new Date().toLocaleTimeString()}</div>
        <div class="edit-button" onclick="editMessage(0, this)">✏️</div>
        <div class="delete-button" onclick="deleteMessage(0, this)">🗑️</div>
    `;
    chatBox.appendChild(userMessage);
    userInput.value = '';
    document.getElementById('charCount').classList.remove('visible');
    chatBox.scrollTop = chatBox.scrollHeight;
    quickReplies.innerHTML = '';
    showNotification('Mensaje enviado', 'success');

    updateStatus('processing');
    const typingIndicator = document.createElement('div');
    typingIndicator.className = 'typing-indicator ai-typing';
    typingIndicator.innerHTML = 'IA escribiendo... <span class="dot"></span><span class="dot"></span><span class="dot"></span>';
    chatBox.appendChild(typingIndicator);
    chatBox.scrollTop = chatBox.scrollHeight;
    const clearProgress = showProgressBar();

    const formData = new FormData();
    formData.append('message', inputMessage);
    if (file) formData.append('file', file);

    try {
        const response = await fetch('/chat', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();
        clearProgress();
        typingIndicator.remove();
        updateStatus('online');

        if (response.ok) {
            socket.emit('new_message', { user_message: inputMessage, ai_response: data.response, timestamp: new Date().toLocaleTimeString(), avatar: document.getElementById('userAvatar').src });
            const aiMessage = document.createElement('div');
            aiMessage.className = 'message ai-message typewriter';
            aiMessage.innerHTML = `
                <div class="avatar">IA</div>
                <div class="message-content"></div>
                <div class="message-timestamp">${new Date().toLocaleTimeString()}</div>
            `;
            chatBox.appendChild(aiMessage);
            const contentDiv = aiMessage.querySelector('.message-content');
            const text = data.response;
            let i = 0;
            function typeWriter() {
                if (i < text.length) {
                    const char = text[i];
                    contentDiv.textContent = text.substring(0, i + 1);
                    contentDiv.innerHTML = marked.parse(contentDiv.textContent);
                    i++;
                    const delay = /[.,!?]/.test(char) ? 50 : 10;
                    setTimeout(typeWriter, delay);
                } else {
                    aiMessage.classList.remove('typewriter');
                    contentDiv.innerHTML = marked.parse(text);
                    if (data.quick_replies) {
                        data.quick_replies.forEach(reply => {
                            const button = document.createElement('button');
                            button.className = 'quick-reply';
                            button.textContent = reply;
                            button.onclick = () => sendMessage(reply);
                            quickReplies.appendChild(button);
                        });
                    }
                }
                chatBox.scrollTop = chatBox.scrollHeight;
            }
            typeWriter();
            checkAchievements();
        } else {
            showNotification(data.error || 'Error al procesar el mensaje', 'error');
        }
    } catch (error) {
        clearProgress();
        typingIndicator.remove();
        updateStatus('online');
        showNotification('Error de conexión: ' + error.message, 'error');
    }
}

function setupFileUpload() {
    const attachButton = document.getElementById('attachButton');
    const fileInput = document.getElementById('fileInput');
    attachButton.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
        const file = fileInput.files[0];
        if (file) sendMessage(null, file);
        fileInput.value = '';
    });
}

function setupSocketIO() {
    socket.on('new_message', (data) => {
        const chatBox = document.getElementById('chatBox');
        const userMessage = document.createElement('div');
        userMessage.className = 'message user-message';
        userMessage.innerHTML = `
            <div class="message-content">${data.user_message}</div>
            <div class="avatar"><img src="${data.avatar}" alt="Avatar"></div>
            <div class="message-timestamp">${data.timestamp}</div>
        `;
        chatBox.appendChild(userMessage);
        const aiMessage = document.createElement('div');
        aiMessage.className = 'message ai-message';
        aiMessage.innerHTML = `
            <div class="avatar">IA</div>
            <div class="message-content">${marked.parse(data.ai_response)}</div>
            <div class="message-timestamp">${data.timestamp}</div>
        `;
        chatBox.appendChild(aiMessage);
        chatBox.scrollTop = chatBox.scrollHeight;
    });
}

async function checkTasks() {
    try {
        const response = await fetch('/tasks');
        const tasks = await response.json();
        if (response.ok && tasks.length) {
            tasks.forEach(task => {
                const now = new Date();
                const taskTime = new Date(task.scheduled_time);
                if (now >= taskTime) {
                    showNotification(`Recordatorio: ${task.description}`, 'info');
                    fetch('/delete_task', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ task_id: task.id })
                    });
                }
            });
        }
    } catch (error) {
        console.error('Error checking tasks:', error);
    }
    setTimeout(checkTasks, 60000); // Check every minute
}

async function checkAchievements() {
    try {
        const response = await fetch('/achievements');
        const achievements = await response.json();
        if (response.ok && achievements.length) {
            achievements.forEach(ach => {
                showNotification(`¡Logro desbloqueado: ${ach.name}! ${ach.description}`, 'achievement');
            });
        }
    } catch (error) {
        console.error('Error checking achievements:', error);
    }
}

function validateLogin() {
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    if (username.length < 3) {
        showNotification('El usuario debe tener al menos 3 caracteres', 'error');
        return false;
    }
    if (password.length < 6) {
        showNotification('La contraseña debe tener al menos 6 caracteres', 'error');
        return false;
    }
    return true;
}

function validateRegister() {
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    if (username.length < 3) {
        showNotification('El usuario debe tener al menos 3 caracteres', 'error');
        return false;
    }
    if (password.length < 6) {
        showNotification('La contraseña debe tener al menos 6 caracteres', 'error');
        return false;
    }
    return true;
}

function setupEmojiPicker() {
    const emojiButton = document.getElementById('emojiButton');
    const emojiPicker = document.getElementById('emojiPicker');
    const userInput = document.getElementById('userInput');
    const picker = new EmojiMart.Picker({
        onEmojiSelect: (emoji) => {
            userInput.value += emoji.native;
            userInput.focus();
            updateCharCount();
        },
        theme: document.body.classList.contains('light') ? 'light' : 'dark',
    });
    emojiPicker.appendChild(picker);
    emojiButton.addEventListener('click', () => {
        emojiPicker.classList.toggle('hidden');
    });
    document.addEventListener('click', (e) => {
        if (!emojiPicker.contains(e.target) && !emojiButton.contains(e.target)) {
            emojiPicker.classList.add('hidden');
        }
    });
}

function setupPlaceholderAnimation() {
    const userInput = document.getElementById('userInput');
    const placeholders = ['Escribe tu mensaje...', 'Pregunta algo...', '¡Explora!'];
    let index = 0;
    setInterval(() => {
        if (!userInput.value && document.activeElement !== userInput) {
            userInput.placeholder = placeholders[index];
            index = (index + 1) % placeholders.length;
        }
    }, 3000);
}

function setupInputEvents() {
    const userInput = document.getElementById('userInput');
    const charCount = document.getElementById('charCount');
    userInput.addEventListener('input', updateCharCount);
    userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    function updateCharCount() {
        const length = userInput.value.length;
        charCount.textContent = `${length}/500`;
        charCount.classList.toggle('visible', length > 0);
    }
}