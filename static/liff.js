// --- 1. 全域變數與狀態管理 ---
const API_BASE_URL = "https://line-nutrition-bot-dev-db-api.onrender.com";
let liffId = "2008021259-NLyg2AWl";

let userToLoad = null;
let operatorId = null;
let isViewingAsAdmin = false;
let userProfileData = {};
let userExercises = [];

let currentDate = new Date();
let autosaveTimer = null;
let trendsChart = null;
let profileAutosaveTimer = null;
let exerciseDbAutosaveTimer = null;
let currentExerciseSession = [];
let profileExerciseSession = [];

const EXERCISE_METS = { '跑步': 8.0, '快走': 4.3, '散步': 3.5, '瑜珈': 2.5 };
const defaultExercisePresets = [
    { type: '跑步', duration: 10 },
    { type: '快走', duration: 10 },
    { type: '散步', duration: 10 },
];


// --- 2. 輔助函式 ---
const formatDate = (date) => new Date(date.getTime() - (date.getTimezoneOffset() * 60000)).toISOString().split('T')[0];

const formatTimestamp = (isoString) => {
    if (!isoString) return '';
    const date = new Date(isoString);
    const year = date.getFullYear();
    const month = (date.getMonth() + 1).toString().padStart(2, '0');
    const day = date.getDate().toString().padStart(2, '0');
    const hours = date.getHours().toString().padStart(2, '0');
    const minutes = date.getMinutes().toString().padStart(2, '0');
    const seconds = date.getSeconds().toString().padStart(2, '0');
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
};

function formatDetailedDate(isoString) {
    if (!isoString) {
        return '--年--月--日 --:--';
    }
    try {
        const date = new Date(isoString);
        const year = date.getFullYear();
        const month = (date.getMonth() + 1).toString().padStart(2, '0');
        const day = date.getDate().toString().padStart(2, '0');
        
        let hours = date.getHours();
        const minutes = date.getMinutes().toString().padStart(2, '0');
        const ampm = hours >= 12 ? '下午' : '上午';
        hours = hours % 12;
        hours = hours ? hours : 12;
        const hoursStr = hours.toString().padStart(2, '0');
        
        return `${year}/${month}/${day} ${ampm} ${hoursStr}:${minutes}`;
    } catch (e) {
        console.error("日期格式化失敗:", e);
        return '--年--月--日 --:--';
    }
}

async function fetchAPI(endpoint, options = {}) {
    const defaultHeaders = {
        'Content-Type': 'application/json',
        'X-Operator-User-Id': operatorId
    };
    options.headers = { ...defaultHeaders, ...options.headers };
    const response = await fetch(`${API_BASE_URL}${endpoint}`, options);
    if (!response.ok) {
        const errorData = await response.json();
        console.error("API Error Response:", errorData);
        const error = new Error(errorData.error || `HTTP error! status: ${response.status}`);
        error.status = response.status;
        throw error;
    }
    const text = await response.text();
    return text ? JSON.parse(text) : {};
}

function showToast(message) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("show");
    setTimeout(() => { toast.classList.remove("show"); }, 2500);
}

// --- 3. 核心功能函式 ---

async function loadUserExercises() {
    if (!userToLoad) return;
    try {
        const exercises = await fetchAPI(`/api/user-exercises?userId=${userToLoad}`);
        userExercises = exercises && exercises.length ? exercises : [];

        const nameInputs = document.querySelectorAll('.user-exercise-name-input');
        const kcalInputs = document.querySelectorAll('.user-exercise-kcal-input');

        nameInputs.forEach(input => input.value = '');
        kcalInputs.forEach(input => input.value = '');

        userExercises.forEach(ex => {
            const index = ex.slot_index - 1;
            if (nameInputs[index]) nameInputs[index].value = ex.exercise_name || '';
            if (kcalInputs[index]) kcalInputs[index].value = ex.kcal || '';
        });

    } catch (error) {
        console.error("讀取個人化運動失敗:", error);
        userExercises = [];
    }
}

async function saveUserExercises() {
    if (!userToLoad) return;
    const exercisesToSave = [
        { name: document.getElementById('user-exercise-name-1').value, kcal: document.getElementById('user-exercise-kcal-1').value },
        { name: document.getElementById('user-exercise-name-2').value, kcal: document.getElementById('user-exercise-kcal-2').value },
        { name: document.getElementById('user-exercise-name-3').value, kcal: document.getElementById('user-exercise-kcal-3').value },
    ];

    try {
        await fetchAPI('/api/user-exercises', {
            method: 'POST',
            body: JSON.stringify({
                userId: userToLoad,
                exercises: exercisesToSave
            })
        });
        showToast('✓ 常用運動已自動儲存');
        userExercises = await fetchAPI(`/api/user-exercises?userId=${userToLoad}`);
        renderUserExerciseButtons();
    } catch (error) {
        console.error("儲存個人化運動失敗:", error);
        showToast('儲存失敗');
    }
}

function triggerExerciseDbAutosave() {
    clearTimeout(exerciseDbAutosaveTimer);
    exerciseDbAutosaveTimer = setTimeout(saveUserExercises, 1500);
}


async function saveProfileData(callback) {
    if (!userToLoad) return;
    const profileData = {
        displayName: userProfileData.displayName,
        height: document.getElementById('height').value,
        weight: document.getElementById('profile-weight').value,
        age: document.getElementById('age').value,
        gender: document.getElementById('gender').value,
        activityLevel: document.getElementById('activity-level').value,
        targetCalories: document.getElementById('target-calories').value,
        waterGoal: document.getElementById('water-goal').value,
        exerciseGoal: document.getElementById('exercise-goal').value,
        exerciseGoalText: document.getElementById('exercise-goal-text').value,
        capsuleGoal: document.getElementById('capsule-goal').value,
        personalNotes: document.getElementById('personal-notes').value,
    };
    try {
        await fetchAPI(`/api/profile?userId=${userToLoad}`, {
            method: 'POST',
            body: JSON.stringify({ data: profileData })
        });
        if (callback) callback();
    } catch (error) {
        console.error("儲存個人檔案失敗:", error);
        showToast('儲存失敗');
    }
}

function triggerProfileAutosave() {
    clearTimeout(profileAutosaveTimer);
    profileAutosaveTimer = setTimeout(() => {
        saveProfileData();
    }, 1500);
}

async function loadProfileData() {
    if (!userToLoad) return;
    const data = await fetchAPI(`/api/profile?userId=${userToLoad}`);
    userProfileData = { ...userProfileData, ...data };

    document.getElementById('height').value = userProfileData.height || '';
    document.getElementById('profile-weight').value = userProfileData.profile_weight || '';
    document.getElementById('age').value = userProfileData.age || '';
    document.getElementById('gender').value = userProfileData.gender || 'male';
    document.getElementById('activity-level').value = userProfileData.activity_level || 1.55;
    document.getElementById('water-goal').value = userProfileData.water_goal || '';
    document.getElementById('exercise-goal').value = userProfileData.exercise_goal || '';
    document.getElementById('exercise-goal-text').value = userProfileData.exercise_goal_text || '';
    document.getElementById('capsule-goal').value = userProfileData.capsule_goal || '';
    document.getElementById('personal-notes').value = userProfileData.personal_notes || '';
    document.getElementById('last-updated').textContent = userProfileData.last_updated ? `上次更新: ${formatTimestamp(userProfileData.last_updated)}` : '';
    document.getElementById('target-calories').value = userProfileData.target_calories || '';

    // ▼▼▼ 新增：載入 AI 分析資料（加在這裡） ▼▼▼
    if (userProfileData.ai_profile_summary) {
        const aiAnalysisContainer = document.getElementById('ai-analysis-content');
        aiAnalysisContainer.innerHTML = userProfileData.ai_profile_summary.replace(/\n/g, '<br>');
    }
    if (userProfileData.ai_analysis_timestamp) {
        const timestampEl = document.getElementById('ai-analysis-timestamp');
        timestampEl.textContent = `分析生成時間：${formatDetailedDate(userProfileData.ai_analysis_timestamp)}`;
    }
    // ▲▲▲ 新增結束 ▲▲▲
    
    // ▼▼▼ 在這裡加入：載入問卷答案 ▼▼▼
    // Part 1: 職業、睡眠、運動、壓力
    if (userProfileData.q_occupation) {
        const radio = document.querySelector(`input[name="q1-occupation"][value="${userProfileData.q_occupation}"]`);
        if (radio) {
            radio.checked = true;
            radio.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }
    if (userProfileData.q_occupation_other) {
        document.getElementById('q1-occupation-other').value = userProfileData.q_occupation_other;
        if (userProfileData.q_occupation === 'E') {
            document.getElementById('q1-occupation-other').classList.remove('hidden');
        }
    }
    if (userProfileData.q_sleep_hours) {
        document.getElementById('q2-sleep-hours').value = userProfileData.q_sleep_hours;
    }
    if (userProfileData.q_exercise_habit) {
        const radio = document.querySelector(`input[name="q3-exercise-habit"][value="${userProfileData.q_exercise_habit}"]`);
        if (radio) {
            radio.checked = true;
            radio.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }
    if (userProfileData.q_stress_level) {
        document.getElementById('q4-stress-level').value = userProfileData.q_stress_level;
        document.getElementById('q4-stress-value').textContent = `${userProfileData.q_stress_level} 分`;
    }
    
    // Part 2: 飲食習慣
    if (userProfileData.q_meal_source) {
        const radio = document.querySelector(`input[name="q5-meal-source"][value="${userProfileData.q_meal_source}"]`);
        if (radio) {
            radio.checked = true;
            radio.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }
    if (userProfileData.q_daily_water) {
        document.getElementById('q6-water-intake').value = userProfileData.q_daily_water;
    }
    if (userProfileData.q_other_drinks) {
        const radio = document.querySelector(`input[name="q7-other-drinks"][value="${userProfileData.q_other_drinks}"]`);
        if (radio) {
            radio.checked = true;
            radio.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }

    // ▼▼▼ 加入這段：載入其他飲料詳細內容 ▼▼▼
    if (userProfileData.q_other_drinks_detail) {
        document.getElementById('q7-other-drinks-detail').value = userProfileData.q_other_drinks_detail;
        if (userProfileData.q_other_drinks === 'D') {
            document.getElementById('q7-other-drinks-detail').classList.remove('hidden');
        }
    }
    // ▲▲▲ 加入結束 ▲▲▲
    
    if (userProfileData.q_snacks_habit) {
        const radio = document.querySelector(`input[name="q8-snacks-habit"][value="${userProfileData.q_snacks_habit}"]`);
        if (radio) {
            radio.checked = true;
            radio.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }
    
    // Part 3: 健康狀況
    if (userProfileData.q_health_conditions) {
        const conditions = userProfileData.q_health_conditions.split(', ');
        conditions.forEach(condition => {
            const checkbox = document.querySelector(`input[name="q9-health-condition"][value="${condition}"]`);
            if (checkbox) {
                checkbox.checked = true;
                checkbox.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
    }
    if (userProfileData.q_allergy_details) {
        document.getElementById('q9-allergy-detail').value = userProfileData.q_allergy_details;
        document.getElementById('q9-allergy-checkbox').checked = true;
        document.getElementById('q9-allergy-detail').classList.remove('hidden');
    }
    if (userProfileData.q_other_illness) {
        document.getElementById('q9-other-condition-detail').value = userProfileData.q_other_illness;
        document.getElementById('q9-other-condition-checkbox').checked = true;
        document.getElementById('q9-other-condition-detail').classList.remove('hidden');
    }
    
    // Part 4: 動機與目標
    if (userProfileData.q_past_challenges) {
        document.getElementById('q10-past-challenges').value = userProfileData.q_past_challenges;
    }
    if (userProfileData.q_motivation) {
        document.getElementById('q11-motivation').value = userProfileData.q_motivation;
    }
    if (userProfileData.q_expected_change) {
        document.getElementById('q12-expected-change').value = userProfileData.q_expected_change;
    }
    // ▲▲▲ 載入問卷答案結束 ▲▲▲
    
    updateProfileCalculations();
    updateMembershipBanner();

    document.getElementById('display-membership-start').textContent = formatDetailedDate(userProfileData.membership_start_date);
    document.getElementById('display-membership-end').textContent = formatDetailedDate(userProfileData.expiry_timestamp);
    document.getElementById('display-membership-termination').textContent = formatDetailedDate(userProfileData.service_termination_date);

    document.querySelectorAll('.goal-button').forEach(btn => btn.classList.remove('selected'));
    if (userProfileData.target_calories) {
        setTimeout(() => {
            const selectedBtn = Array.from(document.querySelectorAll('.goal-button')).find(btn => Number(btn.dataset.finalKcal) === Number(userProfileData.target_calories));
            if (selectedBtn) selectedBtn.classList.add('selected');
        }, 0);
    }
}

function triggerAutosave() {
    clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(async () => {
        if (!userToLoad) return;
        const logData = {};
        document.querySelectorAll('#daily-log-inputs [data-field]').forEach(el => {
            const key = el.dataset.field;
            const value = el.value === '' ? null : el.value;
            logData[key] = value;
        });
        try {
            await fetchAPI(`/api/log`, {
                method: 'POST',
                body: JSON.stringify({
                    userId: operatorId,
                    targetUserId: userToLoad,
                    date: formatDate(currentDate),
                    data: logData
                })
            });
            const dayEl = document.querySelector(`.calendar-day[data-day='${currentDate.getDate()}']`);
            if (dayEl) dayEl.classList.add('completed');
        } catch (error) {
            console.error(`自動儲存 ${formatDate(currentDate)} 的資料失敗:`, error);
            showToast('紀錄儲存失敗');
        }
    }, 1500);
}

async function loadLogDataForDate(date) {
    document.querySelectorAll('#daily-log-inputs [data-field]').forEach(el => { el.value = ''; });
    if (!userToLoad) return;
    try {
        const response = await fetchAPI(`/api/log?userId=${userToLoad}&date=${formatDate(date)}`);
        const logData = response.log_data;
        const prevWeight = response.previous_day_weight;

        if (logData) {
            Object.keys(logData).forEach(key => {
                const el = document.querySelector(`#daily-log-inputs [data-field="${key}"]`);
                if (el) el.value = logData[key] || '';
            });
        }
        renderWeightQuickButtons(prevWeight);
    } catch (error) {
        console.error(`讀取 ${formatDate(date)} 的資料失敗:`, error);
        renderWeightQuickButtons(null);
    } finally {
        currentExerciseSession = [];
        updateGoalDashboard();
    }
}

// ===== ▼▼▼ 1. 新增：處理問卷提交與驗證的核心函式 ▼▼▼ =====
async function handleQuestionnaireSubmit() {
    const submitButton = document.getElementById('submit-questionnaire-btn');
    const questionnaireContainer = document.getElementById('questionnaire-sub-tab');

    // --- Step 1: 必填驗證 ---
    // ===== ▼▼▼ 【修改】修正並補完 requiredFields 中的所有必填欄位 ▼▼▼ =====
    const requiredFields = [
        { id: 'q1-occupation-group', type: 'radio', name: 'q1-occupation', message: '請選擇您的職業性質' },
        { id: 'q2-sleep-hours', type: 'number', message: '請輸入您的平均睡眠時數' },
        { id: 'q3-exercise-habit-group', type: 'radio', name: 'q3-exercise-habit', message: '請選擇您的運動習慣' },
        { id: 'q5-meal-source-group', type: 'radio', name: 'q5-meal-source', message: '請選擇您的三餐來源' },
        { id: 'q6-water-intake', type: 'number', message: '請輸入您的喝水量' },
        { id: 'q7-other-drinks-group', type: 'radio', name: 'q7-other-drinks', message: '請選擇您常喝的飲品' },
        { id: 'q8-snacks-habit-group', type: 'radio', name: 'q8-snacks-habit', message: '請選擇您的點心習慣' },
    ];
    // ===== ▲▲▲ 【修改】結束 ▲▲▲ =====

    let firstErrorElement = null;
    let allValid = true;

    // 清除舊的錯誤提示
    questionnaireContainer.querySelectorAll('.question-item.border-red-500').forEach(el => el.classList.remove('border-red-500', 'border-2'));
    questionnaireContainer.querySelectorAll('input.border-red-500').forEach(el => el.classList.remove('border-red-500', 'border-2'));

    for (const field of requiredFields) {
        let isFieldValid = false;
        let errorElement = null;

        if (field.type === 'radio') {
            const selected = document.querySelector(`input[name="${field.name}"]:checked`);
            if (selected) {
                isFieldValid = true;
            }
            errorElement = document.getElementById(field.id); // 指向選項群組的容器
        } else if (field.type === 'number' || field.type === 'text') {
            const element = document.getElementById(field.id);
            if (element && element.value.trim() !== '') {
                isFieldValid = true;
            }
            errorElement = element;
        }
        
        if (!isFieldValid) {
            allValid = false;
            if (errorElement) {
                const questionItem = errorElement.closest('.question-item');
                if (questionItem) {
                    questionItem.classList.add('border-red-500', 'border-2');
                } else {
                    errorElement.classList.add('border-red-500', 'border-2');
                }

                if (!firstErrorElement) {
                    firstErrorElement = questionItem || errorElement;
                }
            }
        }
    }

    if (!allValid) {
        showToast('哎呀，還有幾個問題沒填完喔！');
        if (firstErrorElement) {
            firstErrorElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        return; 
    }
    
    // --- Step 2: 如果驗證通過，執行提交 ---
    const originalButtonText = submitButton.innerHTML;
    submitButton.disabled = true;
    submitButton.innerHTML = `<svg class="animate-spin h-5 w-5 text-white mx-auto" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>`;

    try {
        const getRadioValue = (name) => document.querySelector(`input[name="${name}"]:checked`)?.value || null;
        const getCheckboxValues = (name) => Array.from(document.querySelectorAll(`input[name="${name}"]:checked`)).map(cb => cb.value);

        const questionnaireData = {
            q1_occupation: getRadioValue('q1-occupation'),
            q1_occupation_other: document.getElementById('q1-occupation-other').value,
            q2_sleep_hours: document.getElementById('q2-sleep-hours').value,
            q3_exercise_habit: getRadioValue('q3-exercise-habit'),
            q4_stress_level: document.getElementById('q4-stress-level').value,
            q5_meal_source: getRadioValue('q5-meal-source'),
            q6_water_intake: document.getElementById('q6-water-intake').value,
            q7_other_drinks: getRadioValue('q7-other-drinks'),
            q7_other_drinks_detail: document.getElementById('q7-other-drinks-detail').value,  // ▼▼▼ 新增 ▼▼▼
            q8_snacks_habit: getRadioValue('q8-snacks-habit'),
            q9_health_conditions: getCheckboxValues('q9-health-condition'),
            q9_allergy_detail: document.getElementById('q9-allergy-detail').value,
            q9_other_condition_detail: document.getElementById('q9-other-condition-detail').value,
            q10_past_challenges: document.getElementById('q10-past-challenges').value,
            q11_motivation: document.getElementById('q11-motivation').value,
            q12_expected_change: document.getElementById('q12-expected-change').value,
        };

        const response = await fetchAPI('/api/summarize-questionnaire', {
            method: 'POST',
            body: JSON.stringify({
                userId: userToLoad,
                questionnaireData: questionnaireData
            })
        });

        if (response && response.ai_summary) {
            const aiAnalysisContainer = document.getElementById('ai-analysis-content');
            aiAnalysisContainer.innerHTML = response.ai_summary.replace(/\n/g, '<br>');
            // ▼▼▼ 新增：顯示分析時間 ▼▼▼
            if (response.ai_analysis_timestamp) {
                const timestampEl = document.getElementById('ai-analysis-timestamp');
                timestampEl.textContent = `分析生成時間：${formatDetailedDate(response.ai_analysis_timestamp)}`;
            }
            // ▲▲▲ 新增結束 ▲▲▲
            showToast('個人化分析已生成！');
            switchSubTab('ai-analysis');
        } else {
            throw new Error('後端未回傳有效的 AI 總結');
        }

    } catch (error) {
        console.error("提交問卷或生成AI分析失敗:", error);
        showToast('分析生成失敗，請稍後再試');
    } finally {
        submitButton.disabled = false;
        submitButton.innerHTML = originalButtonText;
    }
}
// ===== ▲▲▲ 1. 新增結束 ▲▲▲ =====

// ===== ▼▼▼ 新增：處理目標分析提交的函式 ▼▼▼ =====
async function handleGoalAnalysisSubmit() {
    const submitButton = document.getElementById('generate-goal-analysis-btn');
    
    // 收集目標設定資料
    const goalData = {
        height: document.getElementById('height').value,
        weight: document.getElementById('profile-weight').value,
        age: document.getElementById('age').value,
        gender: document.getElementById('gender').value,
        activityLevel: document.getElementById('activity-level').value,
        bmr: userProfileData.bmr,
        tdee: document.getElementById('tdee-display').textContent.replace(' kcal', ''),
        targetCalories: document.getElementById('target-calories').value,
        waterGoal: document.getElementById('water-goal').value,
        exerciseGoal: document.getElementById('exercise-goal').value,
        exerciseGoalText: document.getElementById('exercise-goal-text').value,
        capsuleGoal: document.getElementById('capsule-goal').value,
        personalNotes: document.getElementById('personal-notes').value,
        userExercises: userExercises // 加入常用運動
    };
    
    // 顯示載入中
    const originalButtonText = submitButton.innerHTML;
    submitButton.disabled = true;
    submitButton.innerHTML = `<svg class="animate-spin h-5 w-5 text-white mx-auto" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>`;
    
    try {
        // 呼叫 API 生成分析
        const response = await fetchAPI('/api/generate-goal-analysis', {
            method: 'POST',
            body: JSON.stringify({
                userId: userToLoad,
                goalData: goalData
            })
        });
        
        // 顯示結果
        if (response && response.goal_analysis) {
            document.getElementById('goal-analysis-content').innerHTML = response.goal_analysis.replace(/\n/g, '<br>');
            if (response.goal_analysis_timestamp) {
                document.getElementById('goal-analysis-timestamp').textContent = `分析生成時間：${formatDetailedDate(response.goal_analysis_timestamp)}`;
            }
            showToast('28天執行計畫已生成！');
        } else {
            throw new Error('後端未回傳有效的目標分析');
        }
        
    } catch (error) {
        console.error("生成目標分析失敗:", error);
        showToast('分析生成失敗，請稍後再試');
    } finally {
        submitButton.disabled = false;
        submitButton.innerHTML = originalButtonText;
    }
}
// ===== ▲▲▲ 新增結束 ▲▲▲ =====

// --- 4. UI 更新與事件處理函式 ---
function handleLogInputChange() {
    updateGoalDashboard();
    triggerAutosave();
}

async function showPrevMonth() {
    const today = new Date();
    currentDate.setMonth(currentDate.getMonth() - 1);
    if (currentDate.getFullYear() !== today.getFullYear() || currentDate.getMonth() !== today.getMonth()) {
        currentDate.setDate(1);
    } else {
        currentDate = new Date();
    }
    await renderCalendar(currentDate.getFullYear(), currentDate.getMonth());
    await loadLogDataForDate(currentDate);
}

async function showNextMonth() {
    const today = new Date();
    currentDate.setMonth(currentDate.getMonth() + 1);
    if (currentDate.getFullYear() !== today.getFullYear() || currentDate.getMonth() !== today.getMonth()) {
        currentDate.setDate(1);
    } else {
        currentDate = new Date();
    }
    await renderCalendar(currentDate.getFullYear(), currentDate.getMonth());
    await loadLogDataForDate(currentDate);
}

async function updateChartRange(range, element = null) {
    document.querySelectorAll('.range-btn').forEach(btn => {
        btn.classList.remove('active', 'bg-emerald-500', 'text-white');
        btn.classList.add('bg-gray-200');
    });

    if (element && element.tagName === 'BUTTON') {
        element.classList.add('active', 'bg-emerald-500', 'text-white');
        element.classList.remove('bg-gray-200');
        document.getElementById('start-date-picker').value = '';
        document.getElementById('end-date-picker').value = '';
    }
    
    if (element && (element.id === 'start-date-picker' || element.id === 'end-date-picker')) {
        // No special handling needed
    }

    let params = '';
    if (range === 'custom_date') {
        const startDate = document.getElementById('start-date-picker').value;
        const endDate = document.getElementById('end-date-picker').value;
        if (startDate && endDate) {
            params = `?userId=${userToLoad}&startDate=${startDate}&endDate=${endDate}`;
            await renderChart(params);
        }
        return; 
    } else if (range === 'membership_period') {
        if (userProfileData.membership_start_date) {
            const startDate = userProfileData.membership_start_date.split('T')[0];
            params = `?userId=${userToLoad}&range=${startDate}`;
        } else {
            params = `?userId=${userToLoad}&range=7days`;
            document.querySelector('button[onclick="updateChartRange(\'7days\', this)"]').click();
            return;
        }
    } else {
        params = `?userId=${userToLoad}&range=${range}`;
    }
    
    await renderChart(params);
}


function updateChartVisibility(event) { if (trendsChart) { trendsChart.setDatasetVisibility(event.target.dataset.datasetIndex, event.target.checked); trendsChart.update(); } }

function showAllChartDatasets() {
    if (!trendsChart) return;
    trendsChart.data.datasets.forEach((_, index) => {
        trendsChart.setDatasetVisibility(index, true);
    });
    trendsChart.update();
    document.querySelectorAll('.chart-toggle').forEach(el => el.checked = true);
}

function hideAllChartDatasets() {
    if (!trendsChart) return;
    trendsChart.data.datasets.forEach((_, index) => {
        trendsChart.setDatasetVisibility(index, false);
    });
    trendsChart.update();
    document.querySelectorAll('.chart-toggle').forEach(el => el.checked = false);
}

// ===== ▼▼▼ 2. 修改：switchTab 函式，處理大分頁邏輯 ▼▼▼ =====
function switchTab(tabName) {
    ['profile-main-tab', 'log-tab', 'trends-tab'].forEach(tabId => {
        const tabElement = document.getElementById(tabId);
        if (tabElement) tabElement.classList.add('hidden');
    });

    document.querySelectorAll('.main-tab-button').forEach(button => {
        button.classList.remove('active');
    });

    const targetTabId = (tabName === 'profile' ? 'profile-main-tab' : tabName + '-tab');
    const targetTabElement = document.getElementById(targetTabId);
    if (targetTabElement) targetTabElement.classList.remove('hidden');

    const activeButton = document.querySelector(`.main-tab-button[data-tab="${tabName}"]`);
    if (activeButton) activeButton.classList.add('active');

    if (tabName === 'trends') {
        document.getElementById('calc-footnote').style.display = 'none';
        updateTrendsMembershipInfo();
        renderChart();
    } else if (tabName === 'log') {
        document.getElementById('calc-footnote').style.display = 'block';
        updateGoalDashboard();
    } else if (tabName === 'profile') {
        document.getElementById('calc-footnote').style.display = 'block';
        updateMembershipBanner();
        switchSubTab('goal-setting');
    } else {
        document.getElementById('calc-footnote').style.display = 'none';
    }
}
// ===== ▲▲▲ 2. 修改結束 ▲▲▲ =====


// ===== ▼▼▼ 3. 新增：switchSubTab 函式，處理「我的檔案」底下的小分頁切換 ▼▼▼ =====
function switchSubTab(subTabName) {
    // ▼▼▼ 【修改】加入 goal-analysis-sub-tab 和改名 background-analysis-sub-tab ▼▼▼
    ['goal-setting-sub-tab', 'goal-analysis-sub-tab', 'questionnaire-sub-tab', 'background-analysis-sub-tab'].forEach(subTabId => {
        const subTabElement = document.getElementById(subTabId);
        if (subTabElement) subTabElement.classList.add('hidden');
    });
    // ▲▲▲ 【修改】結束 ▲▲▲

    document.querySelectorAll('.sub-tab-button').forEach(button => {
        button.classList.remove('active');
    });

    const targetSubTabElement = document.getElementById(subTabName + '-sub-tab');
    if (targetSubTabElement) targetSubTabElement.classList.remove('hidden');

    const activeSubButton = document.querySelector(`.sub-tab-button[data-sub-tab="${subTabName}"]`);
    if (activeSubButton) activeSubButton.classList.add('active');
}
// ===== ▲▲▲ 3. 新增結束 ▲▲▲ =====

function updateLocalProfileValue(key, value) {
    userProfileData[key] = value;
}

function updateProfileCalculations() {
    const weight = parseFloat(document.getElementById('profile-weight').value);
    if (weight > 0) {
        document.getElementById('water-suggestion').textContent = `依您的體重，建議飲水量為 ${Math.round(weight * 35)} c.c.`;
    } else {
        document.getElementById('water-suggestion').textContent = '';
    }
    const height = parseFloat(document.getElementById('height').value);
    const age = parseFloat(document.getElementById('age').value);
    const gender = document.getElementById('gender').value;
    const activityLevel = parseFloat(document.getElementById('activity-level').value);

    if (height > 0 && weight > 0 && age > 0) {
        let bmr = (10 * weight) + (6.25 * height) - (5 * age) + (gender === 'male' ? 5 : -161);
        bmr = Math.round(bmr);
        userProfileData.bmr = bmr;
        let tdee = Math.round(bmr * activityLevel);
        document.getElementById('bmr-display').textContent = `${bmr} kcal`;
        document.getElementById('tdee-display').textContent = `${tdee} kcal`;

        const selectedGoalBtn = document.querySelector('.goal-button.selected');
        if (selectedGoalBtn) {
            const goalType = selectedGoalBtn.dataset.goalType;
            let newTargetCalories;
            if (goalType === 'maintain') {
                newTargetCalories = tdee;
            } else if (goalType === 'mild_loss') {
                newTargetCalories = tdee - 300;
            } else if (goalType === 'loss') {
                newTargetCalories = tdee - 500;
            }
            if (newTargetCalories) {
                document.getElementById('target-calories').value = newTargetCalories;
                userProfileData.target_calories = newTargetCalories;
            }
        }

        updateGoalOptions(tdee);

    } else {
        userProfileData.bmr = null;
        document.getElementById('bmr-display').textContent = '---';
        document.getElementById('tdee-display').textContent = '---';
        updateGoalOptions(null);
    }

    updateGoalDashboard();
}

function updateGoalOptions(tdee) {
    const [maintainBtn, mildLossBtn, lossBtn] = document.querySelectorAll('.goal-button');
    if (tdee) {
        const maintainKcal = tdee, mildLossKcal = tdee - 300, lossKcal = tdee - 500;
        maintainBtn.querySelector('span').textContent = `${maintainKcal} kcal`;
        mildLossBtn.querySelector('span:first-of-type').textContent = `${mildLossKcal} kcal`;
        lossBtn.querySelector('span:first-of-type').textContent = `${lossKcal} kcal`;
        document.getElementById('mild-loss-estimate').textContent = `≈ ${((300 * 7) / 7700).toFixed(2)} 公斤/週`;
        document.getElementById('loss-estimate').textContent = `≈ ${((500 * 7) / 7700).toFixed(2)} 公斤/週`;
        maintainBtn.dataset.finalKcal = maintainKcal; mildLossBtn.dataset.finalKcal = mildLossKcal; lossBtn.dataset.finalKcal = lossKcal;
    } else {
        [maintainBtn, mildLossBtn, lossBtn].forEach(btn => btn.querySelector('span:first-of-type').textContent = '---');
        document.getElementById('mild-loss-estimate').textContent = '';
        document.getElementById('loss-estimate').textContent = '';
    }
}

function selectGoal(event) {
    document.querySelectorAll('.goal-button').forEach(btn => btn.classList.remove('selected'));
    const selectedButton = event.currentTarget;
    selectedButton.classList.add('selected');
    const finalKcal = selectedButton.dataset.finalKcal;
    if (finalKcal) {
        const targetCaloriesInput = document.getElementById('target-calories');
        targetCaloriesInput.value = finalKcal;
        updateLocalProfileValue('target_calories', finalKcal);
        updateGoalDashboard();
        triggerProfileAutosave();
    }
}

function getProgressBarColorClass(percentage, isReversed = false) {
    if (percentage <= 0) return 'progress-bar-gray';

    if (isReversed) {
        if (percentage >= 100) return 'progress-bar-red';
        if (percentage >= 95) return 'progress-bar-orange';
        if (percentage >= 90) return 'progress-bar-blue';
        return 'progress-bar-green';
    } else {
        if (percentage >= 100) return 'progress-bar-green';
        if (percentage >= 67) return 'progress-bar-blue';
        if (percentage >= 34) return 'progress-bar-orange';
        return 'progress-bar-red';
    }
}

function updateGoalDashboard() {
    const goalCalories = parseInt(userProfileData.target_calories) || 0;
    const goalWater = parseInt(userProfileData.water_goal) || 0;
    const goalExercise = parseInt(userProfileData.exercise_goal) || 0;
    const goalCapsule = parseInt(userProfileData.capsule_goal) || 0;
    let todayCalories = 0;
    document.querySelectorAll('.calorie-input, [data-field="drinks_kcal"]').forEach(input => { todayCalories += parseInt(input.value) || 0; });
    const todayWater = parseInt(document.querySelector('[data-field="water_cc"]').value) || 0;
    const todayExercise = parseInt(document.querySelector('[data-field="exercise_kcal"]').value) || 0;
    const todayCapsule = parseInt(document.querySelector('[data-field="capsule_qty"]').value) || 0;
    document.querySelector('[data-value="calories-today"]').textContent = todayCalories;
    document.querySelector('[data-value="calories-goal"]').textContent = goalCalories || '...';
    document.querySelector('[data-value="water-today"]').textContent = todayWater;
    document.querySelector('[data-value="water-goal"]').textContent = goalWater || '...';
    document.querySelector('[data-value="exercise-today"]').textContent = todayExercise;
    document.querySelector('[data-value="exercise-goal"]').textContent = goalExercise || '...';
    document.querySelector('[data-value="capsule-today"]').textContent = todayCapsule;
    document.querySelector('[data-value="capsule-goal"]').textContent = goalCapsule || '...';

    const updateProgressBar = (elementId, today, goal, isReversed = false) => {
        const progressBar = document.getElementById(elementId);
        if (!progressBar) return;
        let percentage = goal > 0 ? (today / goal) * 100 : 0;

        const colorClass = getProgressBarColorClass(percentage, isReversed);
        progressBar.style.width = `${Math.min(percentage, 100)}%`;

        progressBar.classList.remove('progress-bar-gray', 'progress-bar-red', 'progress-bar-orange', 'progress-bar-blue', 'progress-bar-green');
        progressBar.classList.add(colorClass);
    };

    updateProgressBar('calories-progress', todayCalories, goalCalories, true);
    updateProgressBar('water-progress', todayWater, goalWater, false);
    updateProgressBar('exercise-progress', todayExercise, goalExercise, false);
    updateProgressBar('capsule-progress', todayCapsule, goalCapsule, false);

    const remainingEl = document.getElementById('calories-remaining');
    if (goalCalories > 0) {
        const remaining = goalCalories - todayCalories;
        if (remaining >= 0) {
            remainingEl.textContent = `還可攝取: ${remaining} kcal`;
            remainingEl.classList.remove('text-red-500');
            remainingEl.classList.add('text-gray-600');
        } else {
            remainingEl.textContent = `已超標: ${-remaining} kcal`;
            remainingEl.classList.add('text-red-500');
            remainingEl.classList.remove('text-gray-600');
        }
    } else {
        remainingEl.textContent = '';
    }
}

async function renderCalendar(year, month) {
    const calendarMonthYear = document.getElementById('calendar-month-year');
    const calendarDays = document.getElementById('calendar-days');
    calendarMonthYear.textContent = `${year}年 ${month + 1}月`;
    calendarDays.innerHTML = '';
    try {
        const daysWithLogs = await fetchAPI(`/api/completion_dots?userId=${userToLoad}&year=${year}&month=${month + 1}`);
        const firstDayOfMonth = new Date(year, month, 1).getDay();
        const daysInMonth = new Date(year, month + 1, 0).getDate();
        const today = new Date();

        for (let i = 0; i < firstDayOfMonth; i++) calendarDays.insertAdjacentHTML('beforeend', '<div></div>');

        for (let day = 1; day <= daysInMonth; day++) {
            const dayEl = document.createElement('div');
            dayEl.textContent = day;
            dayEl.dataset.day = day;
            dayEl.classList.add('calendar-day');

            if (year === today.getFullYear() && month === today.getMonth() && day === today.getDate()) {
                dayEl.classList.add('today');
            }
            if (year === currentDate.getFullYear() && month === currentDate.getMonth() && day === currentDate.getDate()) {
                dayEl.classList.add('selected');
            }
            if (daysWithLogs.includes(day)) {
                dayEl.classList.add('completed');
            }

            dayEl.insertAdjacentHTML('beforeend', '<div class="completion-dot"></div>');
            dayEl.addEventListener('click', async () => {
                currentDate = new Date(year, month, day);
                await loadLogDataForDate(currentDate);
                document.querySelectorAll('.calendar-day.selected').forEach(d => d.classList.remove('selected'));
                dayEl.classList.add('selected');
            });
            calendarDays.appendChild(dayEl);
        }
    } catch (e) { console.error("渲染日曆失敗", e) }
}

async function renderChart(params) {
    if (!params) {
        params = `?userId=${userToLoad}&range=7days`;
    }
    try {
        const apiResponse = await fetchAPI(`/api/trends${params}`);
        const chartData = {
            labels: apiResponse.labels,
            weight: apiResponse.weight,
            calories: apiResponse.calories,
            water: apiResponse.water,
            exercise: apiResponse.exercise,
            capsule: apiResponse.capsule,
        };

        const ctx = document.getElementById('trendsChart').getContext('2d');
        if (trendsChart) trendsChart.destroy();

        const weights = chartData.weight.filter(w => w !== null && w > 0);
        let minWeight = null, maxWeight = null;
        if (weights.length > 0) {
            minWeight = Math.min(...weights) - 0.5;
            maxWeight = Math.max(...weights) + 0.5;
        }

        const rightAxisData = [
            ...chartData.calories, ...chartData.water,
            ...chartData.exercise, ...chartData.capsule
        ].filter(v => v !== null && v > 0);

        const rightAxisMax = rightAxisData.length > 0 ? Math.max(...rightAxisData) : 1000;
        const finalRightAxisMax = Math.ceil((rightAxisMax * 1.2) / 100) * 100;

        trendsChart = new Chart(ctx, {
            data: {
                labels: chartData.labels,
                datasets: [
                    { type: 'line', label: '體重 (kg)', data: chartData.weight, borderColor: '#059669', backgroundColor: 'rgba(16, 185, 129, 0.1)', yAxisID: 'yWeight', tension: 0.1, fill: true, order: 1, borderWidth: 2.5, pointRadius: 4 },
                    { type: 'line', label: '熱量攝取 (kcal)', data: chartData.calories, borderColor: '#ef4444', yAxisID: 'yKcal', order: 2, borderWidth: 2.5, pointRadius: 4, hidden: false },
                    { type: 'line', label: '飲水 (c.c.)', data: chartData.water, borderColor: '#3b82f6', yAxisID: 'yKcal', order: 3, hidden: true },
                    { type: 'line', label: '運動消耗 (kcal)', data: chartData.exercise, borderColor: '#A855F7', yAxisID: 'yKcal', order: 4, hidden: true },
                    {
                        type: 'line',
                        label: '燃脂膠囊 (包)',
                        data: chartData.capsule,
                        borderColor: '#f97316',
                        yAxisID: 'yKcal',
                        stepped: true,
                        order: 5,
                        hidden: true,
                        datalabels: {
                            display: (context) => context.dataset.data[context.dataIndex] > 0,
                            align: 'top',
                            color: '#c2410c',
                            font: { weight: 'bold' }
                        }
                    }
                ]
            },
            plugins: [ChartDataLabels],
            options: {
                responsive: true,
                maintainAspectRatio: true,
                aspectRatio: 1,
                plugins: {
                    datalabels: { display: false }
                },
                scales: {
                    yWeight: {
                        type: 'linear',
                        position: 'left',
                        title: { display: true, text: '公斤 (kg)' },
                        min: minWeight,
                        max: maxWeight,
                    },
                    yKcal: {
                        type: 'linear',
                        position: 'right',
                        title: { display: true, text: 'kcal / c.c. / 包' },
                        grid: { drawOnChartArea: false },
                        min: 0,
                        max: finalRightAxisMax,
                    }
                }
            }
        });

        document.querySelectorAll('.chart-toggle').forEach((el, index) => {
            el.checked = trendsChart.isDatasetVisible(index);
        });

        updateAverages(apiResponse);

    } catch (e) { console.error("渲染圖表失敗", e) }
}

function updateAverages(apiResponse) {
    const averagesDiv = document.getElementById('averages-display');
    if (!averagesDiv) return;

    const averages = apiResponse.averages || {};

    const avgWeight = averages.weight;
    const avgCalories = averages.calories;
    const avgWater = averages.water;
    const avgCapsule = averages.capsule;

    const formatAverage = (avg, unit, decimalPlaces = 1) => {
        if (avg === null || typeof avg === 'undefined') {
            return 'N/A';
        }
        const value = Number(avg).toFixed(decimalPlaces);
        return `${value} <span class="text-xs font-normal">${unit}</span>`;
    };

    document.getElementById('avg-weight').innerHTML = formatAverage(avgWeight, 'kg', 1);
    document.getElementById('avg-calories').innerHTML = formatAverage(avgCalories, 'kcal', 0);
    document.getElementById('avg-water').innerHTML = formatAverage(avgWater, 'c.c.', 0);
    document.getElementById('avg-capsule').innerHTML = formatAverage(avgCapsule, '包', 1);
}


// --- 5. 應用程式啟動流程 ---
function clearInput(fieldName, defaultValue = '') {
    const input = document.querySelector(`[data-field="${fieldName}"]`);
    if (input) {
        input.value = defaultValue;
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }
}

function clearExercise() {
    currentExerciseSession = [];
    clearInput('exercise_text', '');
    clearInput('exercise_kcal', '');
}

function clearProfileExerciseGoal() {
    profileExerciseSession = [];
    const goalInput = document.getElementById('exercise-goal');
    const goalTextInput = document.getElementById('exercise-goal-text');
    goalInput.value = '';
    goalTextInput.value = '';
    goalInput.dispatchEvent(new Event('input', { bubbles: true }));
    goalTextInput.dispatchEvent(new Event('input', { bubbles: true }));
}

function updateExerciseGoalCalculations() {
    const goalInput = document.getElementById('exercise-goal');
    const estimateP = document.getElementById('exercise-goal-estimate');
    const goalKcal = parseInt(goalInput.value) || 0;
    if (goalKcal > 0) {
        const weeklyLoss = ((goalKcal * 7) / 7700).toFixed(2);
        estimateP.textContent = `約等於每週可多消耗 ${weeklyLoss} 公斤`;
    } else {
        estimateP.textContent = '';
    }
}

function handleProfileExerciseQuickAdd(event) {
    const target = event.target.closest('button');
    if (!target) return;
    if (!userProfileData.bmr) {
        alert("請先填寫完整的身高、體重、年齡和性別，才能使用此功能喔！");
        return;
    }
    const type = target.dataset.type;
    const duration = parseInt(target.dataset.duration);
    const existing = profileExerciseSession.find(ex => ex.type === type);
    if (existing) {
        existing.duration += duration;
    } else {
        profileExerciseSession.push({ type, duration });
    }

    let totalCalories = 0;
    let textParts = [];
    profileExerciseSession.forEach(ex => {
        const met = EXERCISE_METS[ex.type];
        totalCalories += Math.round((userProfileData.bmr / 24) * met * (ex.duration / 60));
        textParts.push(`${ex.type} ${ex.duration}分鐘`);
    });

    const goalInput = document.getElementById('exercise-goal');
    const goalTextInput = document.getElementById('exercise-goal-text');
    goalInput.value = totalCalories;
    goalTextInput.value = textParts.join('、');
    goalInput.dispatchEvent(new Event('input', { bubbles: true }));
    goalTextInput.dispatchEvent(new Event('input', { bubbles: true }));
}

function renderProfileQuickExerciseButtons() {
    const container = document.getElementById('profile-quick-add-exercise');
    if (!container) return;
    container.innerHTML = defaultExercisePresets.map(item =>
        `<button class="quick-add-btn" data-type="${item.type}" data-duration="${item.duration}">${item.type} +${item.duration}分</button>`
    ).join('');
}

function renderWeightQuickButtons(prevWeight) {
    const container = document.getElementById('quick-add-weight');
    if (!container) return;
    let buttonsHTML = '';
    if (prevWeight !== null && prevWeight > 0) {
        buttonsHTML += `<button class="quick-add-btn" data-action="set" data-value="${prevWeight}">${prevWeight} kg</button>`;
    }
    buttonsHTML += `<button class="quick-add-btn" data-action="adjust" data-value="-0.1">- 0.1</button>`;
    buttonsHTML += `<button class="quick-add-btn" data-action="adjust" data-value="0.1">+ 0.1</button>`;
    container.innerHTML = buttonsHTML;
}

// ===== ▼▼▼ 【修改】簡化函式，只處理文字不處理樣式 ▼▼▼ =====
function updateQuestionnaireIndicator(inputElement) {
    const questionItem = inputElement.closest('.question-item');
    if (!questionItem) return;

    const indicator = questionItem.querySelector('.selected-answer-indicator');
    if (!indicator) return;

    const inputType = inputElement.getAttribute('type');
    const inputName = inputElement.getAttribute('name');

    // 只處理文字提示，不處理樣式（樣式交給 CSS）
    if (inputType === 'radio') {
        indicator.textContent = inputElement.value;
    } else if (inputType === 'checkbox') {
        const allCheckboxes = questionItem.querySelectorAll(`input[name="${inputName}"]:checked`);
        const values = Array.from(allCheckboxes).map(cb => cb.value);
        indicator.textContent = values.join(', ');
    }
}
// ===== ▲▲▲ 【修改】結束 ▲▲▲ =====

// ===== ▼▼▼ 4. 修改：setupEventListeners 函式，加入新功能事件綁定 ▼▼▼ =====
function setupEventListeners() {
    // ---- 原有事件綁定 (完全不變) ----
    document.querySelectorAll('.profile-input').forEach(el => {
        el.addEventListener('input', (event) => {
            const keyMap = {
                'water-goal': 'water_goal',
                'exercise-goal': 'exercise_goal',
                'exercise-goal-text': 'exercise_goal_text',
                'capsule-goal': 'capsule_goal'
            };
            if (keyMap[event.target.id]) {
                updateLocalProfileValue(keyMap[event.target.id], event.target.value);
            }

            if (el.id === 'exercise-goal-text') {
                triggerProfileAutosave();
            } else {
                updateProfileCalculations();
                triggerProfileAutosave();
            }
        });
    });

    document.querySelectorAll('.goal-button').forEach(btn => btn.addEventListener('click', (e) => selectGoal(e)));
    document.querySelectorAll('.log-input').forEach(el => el.addEventListener('input', handleLogInputChange));
    document.getElementById('prev-month-btn').addEventListener('click', showPrevMonth);
    document.getElementById('next-month-btn').addEventListener('click', showNextMonth);
    document.querySelectorAll('.chart-toggle').forEach(el => el.addEventListener('change', updateChartVisibility));
    
    document.getElementById('start-date-picker').addEventListener('change', () => updateChartRange('custom_date', document.getElementById('start-date-picker')));
    document.getElementById('end-date-picker').addEventListener('change', () => updateChartRange('custom_date', document.getElementById('end-date-picker')));

    const showAllBtn = document.getElementById('show-all-btn');
    if (showAllBtn) showAllBtn.addEventListener('click', showAllChartDatasets);
    const hideAllBtn = document.getElementById('hide-all-btn');
    if (hideAllBtn) hideAllBtn.addEventListener('click', hideAllChartDatasets);

    document.querySelectorAll('.user-exercise-input').forEach(el => {
        el.addEventListener('input', triggerExerciseDbAutosave);
    });

    const appElement = document.getElementById('app');
    if (appElement) {
        appElement.addEventListener('click', (event) => {
            const clearButton = event.target.closest('.clear-input-btn');
            if (clearButton) {
                const targetIdentifier = clearButton.dataset.target;

                if (targetIdentifier === 'exercise_text' || targetIdentifier === 'exercise_kcal') {
                    clearExercise();
                } else {
                    const inputElement = document.querySelector(`[data-field="${targetIdentifier}"]`) || document.getElementById(targetIdentifier);
                    if (inputElement) {
                        inputElement.value = '';
                        inputElement.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                }
            }
        });
    }

    document.getElementById('exercise-goal').addEventListener('input', updateExerciseGoalCalculations);
    document.getElementById('profile-quick-add-exercise').addEventListener('click', handleProfileExerciseQuickAdd);

    document.querySelectorAll('.quick-add-btn[data-amount]').forEach(btn => {
        btn.addEventListener('click', () => {
            const waterInput = document.querySelector('[data-field="water_cc"]');
            const currentWater = parseInt(waterInput.value) || 0;
            const amountToAdd = parseInt(btn.dataset.amount);
            waterInput.value = currentWater + amountToAdd;
            waterInput.dispatchEvent(new Event('input', { bubbles: true }));
        });
    });

    document.getElementById('quick-add-exercise').addEventListener('click', (event) => {
        const target = event.target.closest('button.quick-add-btn');
        if (!target) return;

        const type = target.dataset.type;
        const duration = parseInt(target.dataset.duration);

        const name = target.dataset.name;
        const kcal = parseInt(target.dataset.kcal);

        if (name && !isNaN(kcal)) {
            const textInput = document.querySelector('[data-field="exercise_text"]');
            const kcalInput = document.querySelector('[data-field="exercise_kcal"]');
            const currentText = textInput.value;
            const currentKcal = parseInt(kcalInput.value) || 0;
            textInput.value = currentText ? `${currentText}、${name}` : name;
            kcalInput.value = currentKcal + kcal;
            textInput.dispatchEvent(new Event('input', { bubbles: true }));
            kcalInput.dispatchEvent(new Event('input', { bubbles: true }));
            return;
        }

        if (type && !isNaN(duration)) {
            const bmr = userProfileData.bmr;
            if (!bmr) {
                // 原本的 switchTab('profile') 需要根據新的 HTML 結構進行調整
                // 我們統一導向到'我的檔案'大分頁，並顯示'目標設定'小分頁
                alert("請先到「我的檔案」>「目標設定」頁面填寫完整的身高、體重、年齡和性別，才能使用個人化運動熱量計算功能喔！");
                switchTab('profile'); 
                switchSubTab('goal-setting');
                return;
            }
            const existingExercise = currentExerciseSession.find(ex => ex.type === type);
            if (existingExercise) {
                existingExercise.duration += duration;
            } else {
                currentExerciseSession.push({ type, duration });
            }
            updateExerciseInputs();
        }
    });

    document.getElementById('quick-add-weight').addEventListener('click', (event) => {
        const target = event.target.closest('button');
        if (!target) return;
        const weightInput = document.querySelector('[data-field="daily_weight"]');
        const action = target.dataset.action;
        const value = parseFloat(target.dataset.value);

        if (action === 'set') {
            weightInput.value = value;
        } else if (action === 'adjust') {
            let currentWeight = parseFloat(weightInput.value) || 0;
            currentWeight = parseFloat((currentWeight + value).toFixed(1));
            weightInput.value = Math.max(0, currentWeight);
        }
        weightInput.dispatchEvent(new Event('input', { bubbles: true }));
    });

    document.getElementById('quick-add-capsule').addEventListener('click', () => {
        const capsuleInput = document.querySelector('[data-field="capsule_qty"]');
        const currentQty = parseInt(capsuleInput.value) || 0;
        capsuleInput.value = currentQty + 1;
        capsuleInput.dispatchEvent(new Event('input', { bubbles: true }));
    });

    document.getElementById('get-user-id-btn').addEventListener('click', () => {
        if (operatorId) {
            navigator.clipboard.writeText(operatorId).then(() => {
                alert(`您的 User ID 已複製！\n${operatorId}`);
            }).catch(err => {
                console.error('無法自動複製: ', err);
                prompt("自動複製失敗，請手動複製:", operatorId);
            });
        }
    });

    // ---- 新增事件綁定 ----
    
    // 1. 綁定「我的檔案」底下三個小分頁按鈕的點擊事件
    document.querySelectorAll('.sub-tab-button').forEach(button => {
        button.addEventListener('click', () => {
            const subTabName = button.getAttribute('data-sub-tab');
            switchSubTab(subTabName);
        });
    });

    // 2. 綁定問卷「生成AI分析」按鈕的點擊事件
    const submitBtn = document.getElementById('submit-questionnaire-btn');
    if (submitBtn) {
        submitBtn.addEventListener('click', handleQuestionnaireSubmit);
    }

    // ▼▼▼ 新增：綁定目標分析按鈕 ▼▼▼
    const goalAnalysisBtn = document.getElementById('generate-goal-analysis-btn');
    if (goalAnalysisBtn) {
        goalAnalysisBtn.addEventListener('click', handleGoalAnalysisSubmit);
    }
    // ▲▲▲ 新增結束 ▲▲▲
    
// ===== ▼▼▼ 【修改】簡化事件處理，避免重複 ▼▼▼ =====
    const questionnaireTab = document.getElementById('questionnaire-sub-tab');
    if (questionnaireTab) {
        // 只監聽原生的 change 事件
        questionnaireTab.addEventListener('change', (event) => {
            if (event.target.matches('input[type="radio"], input[type="checkbox"]')) {
                // 更新文字指示器
                updateQuestionnaireIndicator(event.target);
                
                // 處理「其他」選項的顯示/隱藏
                if (event.target.name === 'q1-occupation') {
                    const otherInput = document.getElementById('q1-occupation-other');
                    otherInput.classList.toggle('hidden', event.target.value !== 'E');
                }
                
                if (event.target.name === 'q7-other-drinks') {
                    const otherInput = document.getElementById('q7-other-drinks-detail');
                    otherInput.classList.toggle('hidden', event.target.value !== 'D');
                }
                
                if (event.target.id === 'q9-allergy-checkbox') {
                    const detailInput = document.getElementById('q9-allergy-detail');
                    detailInput.classList.toggle('hidden', !event.target.checked);
                }
                
                if (event.target.id === 'q9-other-condition-checkbox') {
                    const detailInput = document.getElementById('q9-other-condition-detail');
                    detailInput.classList.toggle('hidden', !event.target.checked);
                }
            }
        });


    }
    // ===== ▲▲▲ 【修改】結束 ▲▲▲ =====
}
// ===== ▲▲▲ 4. 修改結束 ▲▲▲ =====

function renderUserExerciseButtons() {
    const container = document.getElementById('quick-add-exercise');
    if (!container) return;

    let htmlContent = '';

    const defaultButtonsHtml = defaultExercisePresets.map(item => {
        return `<button class="quick-add-btn" data-type="${item.type}" data-duration="${item.duration}">${item.type} +${item.duration}分</button>`;
    }).join('');
    htmlContent += defaultButtonsHtml;

    if (userExercises && userExercises.length > 0) {
        const customButtonsHtml = userExercises
            .filter(item => item.exercise_name && item.kcal)
            .map(item =>
                `<button class="quick-add-btn" data-name="${item.exercise_name}" data-kcal="${item.kcal}">${item.exercise_name} +${item.kcal}kcal</button>`
            ).join('');

        if (customButtonsHtml) {
            htmlContent += (htmlContent ? ' ' : '') + customButtonsHtml;
        }
    }

    container.innerHTML = htmlContent;
}


function updateExerciseInputs() {
    let totalCalories = 0;
    let textParts = [];

    const bmr = userProfileData.bmr;
    if (!bmr) return;

    currentExerciseSession.forEach(ex => {
        const met = EXERCISE_METS[ex.type];
        if (met) {
            const calories = Math.round((bmr / 24) * met * (ex.duration / 60));
            totalCalories += calories;
            textParts.push(`${ex.type} ${ex.duration}分鐘`);
        }
    });

    const textInput = document.querySelector('[data-field="exercise_text"]');
    const kcalInput = document.querySelector('[data-field="exercise_kcal"]');

    textInput.value = textParts.join('、');
    kcalInput.value = totalCalories > 0 ? totalCalories : '';

    textInput.dispatchEvent(new Event('input', { bubbles: true }));
    kcalInput.dispatchEvent(new Event('input', { bubbles: true }));
}

function updateMembershipBanner() {
    const displayDiv = document.getElementById('membership-dates-display');
    if (!displayDiv) return;

    const bannerInfo = userProfileData.banner_info;

    if (bannerInfo && bannerInfo.text && bannerInfo.color_class) {
        displayDiv.textContent = bannerInfo.text;
        displayDiv.className = `text-sm px-3 py-1 rounded-full ${bannerInfo.color_class}`;
    } else {
        displayDiv.textContent = '會籍狀態讀取中...';
        displayDiv.className = 'text-sm px-3 py-1 rounded-full bg-gray-100 text-gray-800';
    }
}

function updateTrendsMembershipInfo() {
    const displayDiv = document.getElementById('membership-dates-display-trends');
    if (!displayDiv) return;
    const bannerInfo = userProfileData.banner_info;
    if (bannerInfo && bannerInfo.text && bannerInfo.color_class) {
        displayDiv.textContent = bannerInfo.text;
        displayDiv.className = `text-sm px-3 py-1 rounded-full ${bannerInfo.color_class}`;
    } else {
        displayDiv.textContent = '會籍狀態讀取中...';
        displayDiv.className = 'text-sm px-3 py-1 rounded-full bg-gray-100 text-gray-800';
    }

    document.getElementById('last-updated-trends').textContent = userProfileData.last_updated ? `上次更新: ${formatTimestamp(userProfileData.last_updated)}` : '';

    document.getElementById('display-membership-start-trends').textContent = formatDetailedDate(userProfileData.membership_start_date);
    document.getElementById('display-membership-end-trends').textContent = formatDetailedDate(userProfileData.expiry_timestamp);
    document.getElementById('display-membership-termination-trends').textContent = formatDetailedDate(userProfileData.service_termination_date);
}

async function main() {
    try {
        // ▼▼▼ 加入偵錯 - 在 liff.init 之前 ▼▼▼
        //alert(`初始 URL: ${window.location.href}`);
        
        await liff.init({ liffId });
        
        // ▼▼▼ 加入偵錯 - 在 liff.init 之後 ▼▼▼
        //alert(`LIFF 初始化後 URL: ${window.location.href}`);
        
        if (!liff.isLoggedIn()) {
            liff.login({ redirectUri: window.location.href });
            return;
        }

        // ▼▼▼ 修改這整段 ▼▼▼
        //alert('準備取得 profile...');
        let profile;  // 改成 let，不要 const
        try {
            profile = await liff.getProfile();  // 不要 const
            //alert(`取得 profile 成功: ${profile.userId}`);
        } catch (error) {
            //alert(`取得 profile 失敗: ${error.message}`);
            // 如果取得 profile 失敗，使用空的 profile
            profile = { userId: 'unknown' };  // 不要 const
        }
        // ▲▲▲ 修改結束 ▲▲▲
        
        const urlParams = new URLSearchParams(window.location.search);
        const targetUserIdFromUrl = urlParams.get('targetUserId');
        const operatorIdFromUrl = urlParams.get('operatorId');
        
        // ▼▼▼ 加入偵錯 ▼▼▼
        //alert(`取得的參數:\ntargetUserId: ${targetUserIdFromUrl}\noperatorId: ${operatorIdFromUrl}`);
        
        if (targetUserIdFromUrl && operatorIdFromUrl) {
            isViewingAsAdmin = true;
            userToLoad = targetUserIdFromUrl;
            operatorId = operatorIdFromUrl;
            document.getElementById('admin-view-banner').classList.remove('hidden');
        } else {
            userToLoad = profile.userId;
            operatorId = profile.userId;
            userProfileData.displayName = profile.displayName;
        }

        await loadProfileData();

        updateMembershipBanner();

        if (userProfileData.membership_start_date) {
            document.getElementById('membership-range-btn').classList.remove('hidden');
        } else {
            document.getElementById('membership-range-btn').classList.add('hidden');
        }

        if (!isViewingAsAdmin && !userProfileData.display_name && profile.displayName) {
            fetchAPI(`/api/profile?userId=${userToLoad}`, {
                method: 'POST',
                body: JSON.stringify({ data: { displayName: profile.displayName } })
            }).then(() => {
                userProfileData.display_name = profile.displayName;
                console.log('名稱自動同步成功');
            }).catch(error => console.error('名稱自動同步失敗:', error));
        }

        if (isViewingAsAdmin) {
            document.getElementById('target-user-display-name').textContent = userProfileData.admin_nickname || userProfileData.display_name || '該用戶';
            const adminNotesContent = document.getElementById('admin-notes-content');
            const adminNotesDisplay = document.getElementById('admin-notes-display');
            if (userProfileData.admin_notes) {
                adminNotesContent.textContent = userProfileData.admin_notes;
                adminNotesDisplay.classList.remove('hidden');
            }
        }

        await loadUserExercises();
        renderUserExerciseButtons();
        renderProfileQuickExerciseButtons();
        
        // 預設顯示「每日記錄」大分頁
        switchTab('log');
        
        // 舊的日曆和日誌載入邏輯保持不變，因為它們在「每日記錄」頁
        await renderCalendar(currentDate.getFullYear(), currentDate.getMonth());
        await loadLogDataForDate(currentDate);
        
        setupEventListeners();

        updateTrendsMembershipInfo();

    } catch (error) {
        if (error.status === 403) {
            document.getElementById('access-denied').classList.remove('hidden');
        } else {
            console.error("初始化失敗", error);
            document.getElementById('loading-text').textContent = `初始化失敗: ${error.message}`;
        }
    } finally {
        document.getElementById('loading').classList.add('hidden');
        document.getElementById('app').classList.remove('hidden');
    }
}

// 啟動應用
main();
