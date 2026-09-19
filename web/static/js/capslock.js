/**
 * Caps Lock 안내 — 비밀번호 칸에 포커스가 있을 때 Caps Lock이 켜져 있으면 알려준다.
 *
 * 비밀번호 칸은 입력값이 가려져 있어서 Caps Lock 때문에 틀린 것을 알아채기 어렵고,
 * 로그인은 15분 내 5회 실패하면 10분 잠기기 때문에(web/auth.py) 실수 몇 번이
 * 그대로 잠금으로 이어진다. 로그인 화면과 대시보드가 함께 쓰는 독립 스크립트다.
 *
 * 동작
 *  - 초기화 시점의 `type="password"` 입력칸을 대상으로 잡는다. 표시/숨기기 토글로
 *    type이 text로 바뀌어도 대상에서 빠지지 않도록 data 속성으로 표시해둔다.
 *  - Caps Lock 상태는 KeyboardEvent/MouseEvent 의 getModifierState()로만 알 수 있어
 *    (focus 이벤트에는 없음) 키 입력과 클릭 양쪽에서 갱신한다. 그래서 포커스만 준
 *    직후에는 아직 모를 수 있고, 첫 글자를 누르는 순간 안내가 뜬다.
 *  - 모바일 소프트 키보드에는 Caps Lock 개념이 없어 항상 false — 아무 영향 없다.
 */
(function () {
    'use strict';

    const WARN_TEXT = 'Caps Lock이 켜져 있습니다';

    let capsOn = false;

    /** 안내 문구를 붙일 위치 — 화면 구조에 따라 가장 자연스러운 블록 뒤에 넣는다. */
    function anchorFor(input) {
        return input.closest('.cs-settings-row')        // 설정 탭: 라벨+입력 한 줄 뒤
            || input.closest('.cs-settings-input-wrap') // 입력+토글 묶음 뒤
            || input;                                   // 로그인 화면 등: 입력칸 바로 뒤
    }

    function warnElFor(input) {
        if (input._csCapsWarn && input._csCapsWarn.isConnected) return input._csCapsWarn;
        const el = document.createElement('div');
        el.className = 'cs-capslock-warn';
        el.setAttribute('role', 'status');
        el.hidden = true;
        el.innerHTML =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M12 3 4 11h4v5h8v-5h4z"/><rect x="8" y="18" width="8" height="3" rx="1"/></svg>' +
            '<span></span>';
        el.querySelector('span').textContent = WARN_TEXT;
        anchorFor(input).insertAdjacentElement('afterend', el);
        input._csCapsWarn = el;
        return el;
    }

    function render() {
        document.querySelectorAll('input[data-cs-pwd-field]').forEach(input => {
            const show = capsOn && document.activeElement === input;
            const el = warnElFor(input);
            if (el.hidden !== !show) el.hidden = !show;
        });
    }

    function update(e) {
        // getModifierState 가 없는 이벤트(예: 일부 합성 이벤트)는 상태를 건드리지 않는다
        if (!e || typeof e.getModifierState !== 'function') return;
        const next = e.getModifierState('CapsLock');
        if (next === capsOn) return;
        capsOn = next;
        render();
    }

    function init() {
        document.querySelectorAll('input[type="password"]').forEach(input => {
            input.dataset.csPwdField = '1';
            warnElFor(input);   // 미리 만들어 둔다 — hidden 상태라 자리는 차지하지 않는다
        });
        // 키 입력/클릭에서 Caps Lock 상태를 읽는다 (focus 이벤트로는 알 수 없음)
        document.addEventListener('keydown',   update, true);
        document.addEventListener('keyup',     update, true);
        document.addEventListener('mousedown', update, true);
        // 포커스가 옮겨가면 해당 칸의 안내는 감춘다
        document.addEventListener('focusin',  render, true);
        document.addEventListener('focusout', () => setTimeout(render, 0), true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
