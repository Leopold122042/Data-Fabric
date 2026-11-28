function dictationHint() {
  const platform = navigator.userAgentData?.platform || navigator.platform || '';
  if (/Android|iPhone|iPad/i.test(navigator.userAgent)
      || (/Mac/i.test(platform) && navigator.maxTouchPoints > 1)) {
    return '请点手机键盘上的麦克风开始听写。';
  }
  if (/Win/i.test(platform)) return '可按 Win + H 使用 Windows 语音输入。';
  if (/Mac/i.test(platform)) return '可按 Mac 的听写键，或从「编辑」菜单开始听写。';
  return '可在输入框中使用系统或输入法的语音输入。';
}

const errors = {
  'not-allowed': '无法使用浏览器麦克风，请查看此站点的麦克风权限。',
  'service-not-allowed': '浏览器暂不允许使用语音识别服务。',
  'audio-capture': '浏览器没有找到可用的麦克风。',
  'network': '浏览器的语音识别服务连接失败。',
  'language-not-supported': '当前浏览器无法识别普通话。',
  'no-speech': '没有听到清楚的语音，请再试一次。',
  'aborted': '语音输入已停止。'
};

export function mountVoiceInput(form) {
  const input = form.querySelector('textarea');
  const button = form.querySelector('#voice-input');
  const label = button.querySelector('span');
  const status = form.querySelector('#voice-status');
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let session = null;
  let phase = 'idle';

  function display(next, message = '') {
    phase = next;
    label.textContent = next === 'starting' ? '取消语音'
      : next === 'listening' ? '结束语音'
      : next === 'finishing' ? '识别中…' : '语音输入';
    button.setAttribute('aria-pressed', String(next !== 'idle'));
    button.disabled = next === 'finishing';
    status.textContent = message;
  }

  function useDictation(message = '') {
    display('idle', [message, dictationHint()].filter(Boolean).join(' '));
    input.focus();
  }

  function cancel() {
    const current = session;
    session = null;
    clearTimeout(current?.timeout);
    if (current) {
      try { current.recognition.abort(); } catch {}
    }
  }

  function addTranscript(text) {
    if (!text) {
      useDictation('没有识别到文字，可以再试一次。');
      return;
    }
    const end = input.value.length;
    const separator = end && !/\s$/.test(input.value) ? ' ' : '';
    const available = input.maxLength - end - separator.length;
    if (available < 1) {
      display('idle', '查询已达到字数上限，请编辑后再继续。');
      input.focus();
      return;
    }
    // Preserve any text typed while the microphone was active.
    input.setRangeText(separator + text.slice(0, available), end, end, 'end');
    input.dispatchEvent(new Event('input', {bubbles: true}));
    display('idle', text.length > available ? '已填入可容纳的文字，请核对后提交。' : '语音已转成文字，可以编辑后提交。');
    input.focus();
  }

  button.onclick = () => {
    if (session) {
      if (phase === 'starting') {
        cancel();
        display('idle', '语音输入已取消。');
      } else if (phase === 'listening') {
        display('finishing', '正在整理识别结果…');
        try { session.recognition.stop(); }
        catch {
          const text = session?.transcript;
          cancel();
          addTranscript(text);
        }
      }
      return;
    }
    if (!Recognition) {
      useDictation('当前浏览器使用系统听写输入。');
      return;
    }
    if (input.value.length >= input.maxLength) {
      display('idle', '查询已达到字数上限，请编辑后再继续。');
      return;
    }

    let recognition;
    try { recognition = new Recognition(); }
    catch {
      useDictation('浏览器语音识别未能启动。');
      return;
    }
    const current = {recognition, transcript: '', timeout: null};
    recognition.lang = 'zh-CN';
    recognition.continuous = false;
    session = current;
    display('starting', '请允许使用麦克风。语音由浏览器识别，可能使用在线服务。');
    recognition.onaudiostart = () => {
      if (session === current) display('listening', '正在听，请说出你的查询需求。');
    };
    recognition.onresult = event => {
      if (session !== current) return;
      current.transcript = Array.from(event.results)
        .filter(result => result.isFinal)
        .map(result => result[0].transcript)
        .join('')
        .trim();
    };
    recognition.onend = () => {
      if (session !== current) return;
      session = null;
      clearTimeout(current.timeout);
      addTranscript(current.transcript);
    };
    recognition.onerror = event => {
      if (session !== current) return;
      session = null;
      clearTimeout(current.timeout);
      if (current.transcript) {
        addTranscript(current.transcript);
      } else if (['no-speech', 'aborted'].includes(event.error)) {
        display('idle', errors[event.error]);
      } else {
        const message = event.error === 'not-allowed' && !window.isSecureContext
          ? '浏览器限制了当前网页的麦克风。' : errors[event.error] || '浏览器暂时无法识别语音。';
        useDictation(message);
      }
    };
    try {
      recognition.start();
      if (session === current) current.timeout = setTimeout(() => {
        if (session !== current) return;
        cancel();
        if (current.transcript) addTranscript(current.transcript);
        else useDictation('浏览器语音识别已超时，可重试。');
      }, 45_000);
    } catch {
      cancel();
      useDictation('浏览器语音识别未能启动。');
    }
  };

  form.querySelector('#voice-dictation').onclick = () => {
    cancel();
    useDictation();
  };
  return {
    readyToSubmit() {
      if (!session) return true;
      status.textContent = '请先结束语音输入，核对文字后再提交。';
      return false;
    },
    dispose() {
      cancel();
      display('idle');
    }
  };
}
