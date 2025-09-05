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
let exerciseDbAutosaveTimer = null; // [NEW] 用於自訂運動自動儲存的計時器
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
        throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
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
        
        // [NEW] 讀取後，將資料填入個人檔案的設定區
        const nameInputs = document.querySelectorAll('.user-exercise-name-input');
        const kcalInputs = document.querySelectorAll('.user-exercise-kcal-input');
        
        userExercises.forEach((ex, i) => {
            if(nameInputs[i]) nameInputs[i].value = ex.exercise_name || '';
            if(kcalInputs[i]) kcalInputs[i].value = ex.kcal || '';
        });

    } catch (error) {
        console.error("讀取個人化運動失敗:", error);
        userExercises = [];
    }
}

// [MODIFIED] 修改為自動儲存，不再有點擊觸發
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
        userExercises = await fetchAPI(`/api/user-exercises?userId=${userToLoad}`); // 重新從後端獲取最新資料確保同步
        renderUserExerciseButtons(); 
    } catch (error) {
        console.error("儲存個人化運動失敗:", error);
        showToast('儲存失敗');
    }
}

// [NEW] 自動儲存自訂運動的觸發器
function triggerExerciseDbAutosave() {
    clearTimeout(exerciseDbAutosaveTimer);
    exerciseDbAutosaveTimer = setTimeout(saveUserExercises, 1500); // 延遲1.5秒
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
    try {
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

        updateProfileCalculations();

        document.querySelectorAll('.goal-button').forEach(btn => btn.classList.remove('selected'));
        if (userProfileData.target_calories) {
            // [MODIFIED] 改為使用 setTimeout 確保 TDEE 計算完成後再執行
            setTimeout(() => {
                const selectedBtn = Array.from(document.querySelectorAll('.goal-button')).find(btn => Number(btn.dataset.finalKcal) === Number(userProfileData.target_calories));
                if (selectedBtn) selectedBtn.classList.add('selected');
            }, 0);
        }
    } catch (error) {
        console.error("讀取個人檔案失敗:", error);
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
            if(dayEl) dayEl.classList.add('completed');
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
    if (element) {
        if (element.tagName === 'BUTTON') {
            element.classList.add('active', 'bg-emerald-500', 'text-white');
            element.classList.remove('bg-gray-200');
        }
    }

    let finalRange = range;
    if (range === 'membership_period') {
        if (userProfileData.membership_start_date) {
            finalRange = userProfileData.membership_start_date.split('T')[0];
        } else {
            finalRange = '7days';
            document.querySelector('button[onclick="updateChartRange(\'7days\', this)"]').click();
        }
    }
    await renderChart(finalRange);
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


function switchTab(tabName) {
    ['profile', 'log', 'trends'].forEach(tabId => {
        document.getElementById(tabId + '-tab').classList.add('hidden');
        document.querySelector(`button[onclick="switchTab('${tabId}')"]`).classList.remove('active', 'text-gray-900');
    });
    document.getElementById(tabName + '-tab').classList.remove('hidden');
    const button = document.querySelector(`button[onclick="switchTab('${tabName}')"]`);
    button.classList.add('active', 'text-gray-900');

    if (tabName === 'trends') {
        document.getElementById('calc-footnote').style.display = 'none';
        renderChart();
    } else if (tabName === 'log') {
        document.getElementById('calc-footnote').style.display = 'block';
        updateGoalDashboard();
    } else if (tabName === 'profile') {
        document.getElementById('calc-footnote').style.display = 'block';
    } else {
        document.getElementById('calc-footnote').style.display = 'none';
    }
}

function updateLocalProfileValue(key, value) {
    userProfileData[key] = value;
}

// [MODIFIED] Bug 修正與即時連動 - 這是最終修正版
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
        
        // [BUG FIX] 當 TDEE 變化時，自動更新目標卡路里
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
        document.getElementById('mild-loss-estimate').textContent = `≈ ${((300*7)/7700).toFixed(2)} 公斤/週`;
        document.getElementById('loss-estimate').textContent = `≈ ${((500*7)/7700).toFixed(2)} 公斤/週`;
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
        const exerciseBurn = parseInt(document.querySelector('[data-field="exercise_kcal"]').value) || 0;
        const remaining = goalCalories - todayCalories + exerciseBurn;
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
    } catch(e) { console.error("渲染日曆失敗", e) }
}

async function renderChart(range = '7days') {
    try {
        const chartData = await fetchAPI(`/api/trends?userId=${userToLoad}&range=${range}`);
        const ctx = document.getElementById('trendsChart').getContext('2d');
        if (trendsChart) trendsChart.destroy();

        const weights = chartData.weight.filter(w => w !== null && w > 0);
        let minWeight = null;
        let maxWeight = null;
        if (weights.length > 0) {
            const dataMin = Math.min(...weights);
            const dataMax = Math.max(...weights);
            minWeight = dataMin - 0.5;
            maxWeight = dataMax + 0.5;
        }

        trendsChart = new Chart(ctx, {
            data: {
                labels: chartData.labels,
                datasets: [
                    { type: 'line', label: '體重 (kg)', data: chartData.weight, borderColor: '#059669', backgroundColor: 'rgba(16, 185, 129, 0.1)', yAxisID: 'yWeight', tension: 0.1, fill: true, order: 1 },
                    { type: 'line', label: '熱量攝取 (kcal)', data: chartData.calories, borderColor: '#ef4444', yAxisID: 'yKcal', order: 2 },
                    { type: 'line', label: '飲水 (c.c.)', data: chartData.water, borderColor: '#3b82f6', yAxisID: 'yKcal', order: 2 },
                    { type: 'line', label: '運動消耗 (kcal)', data: chartData.exercise, borderColor: '#A855F7', yAxisID: 'yKcal', order: 2 },
                    {
                        type: 'line',
                        label: '燃脂膠囊 (包)',
                        data: chartData.capsule,
                        borderColor: '#f97316',
                        backgroundColor: 'rgba(249, 115, 22, 0.2)',
                        yAxisID: 'yKcal',
                        stepped: true,
                        fill: false,
                        pointRadius: 5,
                        pointBackgroundColor: '#f97316',
                        order: 3,
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
                    datalabels: {
                        display: false,
                    }
                },
                scales: {
                    yWeight: {
                        type: 'linear',
                        position: 'left',
                        title: { display: true, text: '公斤 (kg)' },
                        min: minWeight,
                        max: maxWeight,
                        ticks: { stepSize: 0.1 }
                    },
                    yKcal: {
                        type: 'linear',
                        position: 'right',
                        title: { display: true, text: 'kcal / c.c. / 包' },
                        grid: { drawOnChartArea: false },
                        beginAtZero: true
                    }
                }
            }
        });

        document.querySelectorAll('.chart-toggle').forEach(el => {
            const isVisible = trendsChart.isDatasetVisible(el.dataset.datasetIndex);
            if (isVisible !== el.checked) {
                trendsChart.setDatasetVisibility(el.dataset.datasetIndex, el.checked);
            }
        });
        trendsChart.update();
        updateAverages(chartData);

    } catch (e) { console.error("渲染圖表失敗", e) }
}

function updateAverages(chartData) {
    const averagesDiv = document.getElementById('averages-display');
    if (!averagesDiv) return;

    const calculateAverage = (data) => {
        const validData = data.filter(item => item !== null && typeof item === 'number');
        if (validData.length === 0) return 'N/A';
        const sum = validData.reduce((a, b) => a + b, 0);
        const avg = sum / validData.length;
        if (['weight', 'capsule'].some(key => chartData[key] === data)) {
            return avg.toFixed(1);
        }
        return Math.round(avg);
    };

    const avgWeight = calculateAverage(chartData.weight);
    const avgCalories = calculateAverage(chartData.calories);
    const avgWater = calculateAverage(chartData.water);
    const avgCapsule = calculateAverage(chartData.capsule);

    document.getElementById('avg-weight').innerHTML = avgWeight !== 'N/A' ? `${avgWeight} <span class="text-xs font-normal">kg</span>` : 'N/A';
    document.getElementById('avg-calories').innerHTML = avgCalories !== 'N/A' ? `${avgCalories} <span class="text-xs font-normal">kcal</span>` : 'N/A';
    document.getElementById('avg-water').innerHTML = avgWater !== 'N/A' ? `${avgWater} <span class="text-xs font-normal">c.c.</span>` : 'N/A';
    document.getElementById('avg-capsule').innerHTML = avgCapsule !== 'N/A' ? `${avgCapsule} <span class="text-xs font-normal">包</span>` : 'N/A';
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


function setupEventListeners() {
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

            if(el.id === 'exercise-goal-text') {
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
    document.getElementById('start-date-picker').addEventListener('change', (e) => updateChartRange(e.target.value, e.target));
    
    const showAllBtn = document.getElementById('show-all-btn');
    if (showAllBtn) showAllBtn.addEventListener('click', showAllChartDatasets);
    const hideAllBtn = document.getElementById('hide-all-btn');
    if (hideAllBtn) hideAllBtn.addEventListener('click', hideAllChartDatasets);
    
    // [MODIFIED] 自訂運動改為自動儲存
    document.querySelectorAll('.user-exercise-input').forEach(el => {
        el.addEventListener('input', triggerExerciseDbAutosave);
    });
    
    const appElement = document.getElementById('app');
    if(appElement) {
        appElement.addEventListener('click', (event) => {
            if (event.target.classList.contains('clear-input-btn')) {
                // [MODIFIED] 修正 data-target 的邏輯，使其可以對應 data-field
                const targetIdentifier = event.target.dataset.target;
                const inputElement = document.querySelector(`[data-field="${targetIdentifier}"]`) || document.getElementById(targetIdentifier);
                if (inputElement) {
                    inputElement.value = '';
                    inputElement.dispatchEvent(new Event('input', { bubbles: true }));
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
        const target = event.target.closest('button');
        if (!target) return;

        // [MODIFIED] 處理預設運動和自訂運動的點擊
        const type = target.dataset.type; // 預設運動
        const duration = parseInt(target.dataset.duration); // 預設運動
        
        const name = target.dataset.name; // 自訂運動
        const kcal = parseInt(target.dataset.kcal); // 自訂運動

        // 處理自訂運動
        if (name && !isNaN(kcal)) {
            const textInput = document.querySelector('[data-field="exercise_text"]');
            const kcalInput = document.querySelector('[data-field="exercise_kcal"]');
            const currentText = textInput.value;
            const currentKcal = parseInt(kcalInput.value) || 0;

            textInput.value = currentText ? `${currentText}、${name}` : name;
            kcalInput.value = currentKcal + kcal;

            textInput.dispatchEvent(new Event('input', { bubbles: true }));
            kcalInput.dispatchEvent(new Event('input', { bubbles: true }));
            return; // 處理完畢，結束函式
        }

        // 處理預設運動
        if (type && !isNaN(duration)) {
             const bmr = userProfileData.bmr;
            if (!bmr) {
                alert("請先到「個人檔案」頁面填寫完整的身高、體重、年齡和性別，才能使用個人化運動熱量計算功能喔！");
                switchTab('profile');
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
}

// [REMOVED] 此函式已被 renderUserExerciseButtons 取代和合併，故移除
// function renderQuickExerciseButtons() { ... }

// [MODIFIED] 根據您的要求，同時渲染預設運動和自訂運動
function renderUserExerciseButtons() {
    const container = document.getElementById('quick-add-exercise');
    if (!container) return;

    let htmlContent = '';

    // 1. 渲染預設運動按鈕 (使用 BMR 計算)
    if (userProfileData.bmr) {
        const defaultButtonsHtml = defaultExercisePresets.map(item => {
            const met = EXERCISE_METS[item.type];
            const kcal = Math.round((userProfileData.bmr / 24) * met * (item.duration / 60));
            // [MODIFIED] 預設按鈕也改為直接加總卡路里，簡化邏輯
            return `<button class="quick-add-btn" data-name="${item.type} ${item.duration}分鐘" data-kcal="${kcal}">${item.type} +${item.duration}分</button>`;
        }).join('');
        htmlContent += defaultButtonsHtml;
    }

    // 2. 渲染使用者自訂運動按鈕
    if (userExercises && userExercises.length > 0) {
        const customButtonsHtml = userExercises
            .filter(item => item.exercise_name && item.kcal) 
            .map(item =>
                `<button class="quick-add-btn" data-name="${item.exercise_name}" data-kcal="${item.kcal}">${item.exercise_name} +${item.kcal}kcal</button>`
            ).join('');
        
        if (customButtonsHtml) {
             htmlContent += (htmlContent ? ' ' : '') + customButtonsHtml; // 如果已有預設按鈕，加個空格
        }
    }
    
    if (!htmlContent) {
        container.innerHTML = `<p class="text-xs text-gray-500">請先在「個人檔案」頁面填寫身體數據以顯示預設運動，或設定您的常用運動。</p>`;
    } else {
        container.innerHTML = htmlContent;
    }
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

    const { status, days_remaining } = userProfileData;

    let fullText = '會籍狀態';
    let labelClass = 'bg-gray-100 text-gray-800';

    if (status === 'Active') {
        labelClass = 'bg-green-100 text-green-800';
        if (typeof days_remaining === 'number' && days_remaining >= 0) {
            fullText = `會籍有效 | 剩下 ${days_remaining} 天，繼續加油！`;
        } else {
            fullText = '會籍有效';
        }
    } else if (status === 'Trial') {
        labelClass = 'bg-yellow-100 text-yellow-800';
        if (typeof days_remaining === 'number' && days_remaining >= 0) {
            fullText = `體驗中 | 剩下 ${days_remaining} 天，把握機會！`;
        } else {
            fullText = '體驗中 (請洽管理員)';
        }
    } else { 
        labelClass = 'bg-red-100 text-red-800';
        fullText = '會籍已失效 (請洽管理員)';
    }

    displayDiv.textContent = fullText;
    displayDiv.className = `text-sm px-3 py-1 rounded-full ${labelClass}`;
}


async function main() {
    try {
        await liff.init({ liffId });
        if (!liff.isLoggedIn()) {
            liff.login({ redirectUri: window.location.href });
            return;
        }

        const profile = await liff.getProfile();
        const urlParams = new URLSearchParams(window.location.search);
        const targetUserIdFromUrl = urlParams.get('targetUserId');
        const operatorIdFromUrl = urlParams.get('operatorId');

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

        if (!isViewingAsAdmin) {
            if (userProfileData.status === 'Terminated') {
                 document.getElementById('loading').classList.add('hidden');
                 document.getElementById('access-denied').classList.remove('hidden');
                 return;
            }
             if (userProfileData.status === 'Trial') {
                 document.getElementById('trial-banner').classList.remove('hidden');
            }
        }

        updateMembershipBanner();
        
        if (userProfileData.membership_start_date) {
            document.getElementById('membership-range-btn').classList.remove('hidden');
        } else {
            document.getElementById('membership-range-btn').classList.add('hidden');
        }

        if (!isViewingAsAdmin && !userProfileData.display_name && profile.displayName) {
            console.log('偵測到首次登入或名稱未同步，自動更新名稱...');
            try {
                await fetchAPI(`/api/profile?userId=${userToLoad}`, {
                    method: 'POST',
                    body: JSON.stringify({ data: { displayName: profile.displayName } })
                });
                userProfileData.display_name = profile.displayName;
            } catch (error) { console.error('名稱自動同步失敗:', error); }
        }

        if(isViewingAsAdmin) {
            document.getElementById('target-user-display-name').textContent = userProfileData.admin_nickname || userProfileData.display_name || '該用戶';
            const adminNotesContent = document.getElementById('admin-notes-content');
            const adminNotesDisplay = document.getElementById('admin-notes-display');
            if(userProfileData.admin_notes) {
                adminNotesContent.textContent = userProfileData.admin_notes;
                adminNotesDisplay.classList.remove('hidden');
            }
        }
        
        await loadUserExercises();

        renderUserExerciseButtons();
        renderProfileQuickExerciseButtons();

        document.querySelector('button[onclick="switchTab(\'log\')"]').classList.add('active','text-gray-900');
        document.getElementById('log-tab').classList.remove('hidden');
        document.getElementById('calc-footnote').style.display = 'block';

        await renderCalendar(currentDate.getFullYear(), currentDate.getMonth());
        await loadLogDataForDate(currentDate);
        setupEventListeners();

    } catch (error) {
        console.error("初始化失敗", error);
        document.getElementById('loading-text').textContent = `初始化失敗: ${error.message}`;
    } finally {
        document.getElementById('loading').classList.add('hidden');
        document.getElementById('app').classList.remove('hidden');
    }
}

// 啟動應用
main();
