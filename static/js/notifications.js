/**
 * Workflow Notification System
 * 
 * Polls for workflow state changes and displays notifications via:
 * - Header badge with unread count
 * - Dropdown panel with recent notifications
 * - Toast notifications for new items
 */

(function() {
    'use strict';

    // Configuration
    const POLL_INTERVAL_MS = 15000; // 15 seconds
    const TOAST_DURATION_MS = 5000; // 5 seconds
    const MAX_DROPDOWN_ITEMS = 20;
    const STORAGE_KEYS = {
        lastPollTime: 'notifications.lastPollTime',
        seenIds: 'notifications.seenIds',
    };

    // State
    let pollIntervalId = null;
    let isPolling = false;
    let notifications = [];

    // -------------------------------------------------------------------------
    // LocalStorage Helpers
    // -------------------------------------------------------------------------

    function getLastPollTime() {
        const stored = localStorage.getItem(STORAGE_KEYS.lastPollTime);
        if (stored) {
            return stored;
        }
        // Default to current time on first load
        const now = new Date().toISOString();
        localStorage.setItem(STORAGE_KEYS.lastPollTime, now);
        return now;
    }

    function setLastPollTime(isoString) {
        localStorage.setItem(STORAGE_KEYS.lastPollTime, isoString);
    }

    function getSeenIds() {
        const stored = localStorage.getItem(STORAGE_KEYS.seenIds);
        if (stored) {
            try {
                return new Set(JSON.parse(stored));
            } catch (e) {
                return new Set();
            }
        }
        return new Set();
    }

    function addSeenIds(ids) {
        const seen = getSeenIds();
        ids.forEach(id => seen.add(id));
        // Keep only last 500 IDs to prevent unbounded growth
        const arr = Array.from(seen).slice(-500);
        localStorage.setItem(STORAGE_KEYS.seenIds, JSON.stringify(arr));
    }

    // -------------------------------------------------------------------------
    // API
    // -------------------------------------------------------------------------

    async function fetchNotifications(since) {
        try {
            const response = await fetch(`/api/v1/notifications/poll?since=${encodeURIComponent(since)}`);
            if (!response.ok) {
                console.error('Notification poll failed:', response.status);
                return [];
            }
            return await response.json();
        } catch (error) {
            console.error('Notification poll error:', error);
            return [];
        }
    }

    // -------------------------------------------------------------------------
    // Polling
    // -------------------------------------------------------------------------

    async function poll() {
        if (isPolling) return;
        isPolling = true;

        try {
            const since = getLastPollTime();
            const newNotifications = await fetchNotifications(since);

            if (newNotifications.length > 0) {
                // Update last poll time to the most recent notification
                const latestTime = newNotifications[0].created_datetime;
                if (latestTime) {
                    setLastPollTime(latestTime);
                }

                // Find truly new notifications (not seen before)
                const seenIds = getSeenIds();
                const unseenNotifications = newNotifications.filter(n => !seenIds.has(n.id));

                // Show toast for each new notification
                unseenNotifications.forEach(notification => {
                    showToast(notification);
                });

                // Add to our notifications list (newest first)
                notifications = [...newNotifications, ...notifications].slice(0, MAX_DROPDOWN_ITEMS);

                // Update UI
                updateBadge();
                updateDropdown();
            }
        } finally {
            isPolling = false;
        }
    }

    function startPolling() {
        if (pollIntervalId) return;
        
        // Poll immediately on start
        poll();
        
        // Then poll at interval
        pollIntervalId = setInterval(poll, POLL_INTERVAL_MS);
    }

    function stopPolling() {
        if (pollIntervalId) {
            clearInterval(pollIntervalId);
            pollIntervalId = null;
        }
    }

    // -------------------------------------------------------------------------
    // Visibility API - Pause/Resume polling
    // -------------------------------------------------------------------------

    function handleVisibilityChange() {
        if (document.hidden) {
            stopPolling();
        } else {
            startPolling();
        }
    }

    // -------------------------------------------------------------------------
    // UI Updates
    // -------------------------------------------------------------------------

    function updateBadge() {
        const badge = document.getElementById('notification-badge');
        if (!badge) return;

        const seenIds = getSeenIds();
        const unreadCount = notifications.filter(n => !seenIds.has(n.id)).length;

        if (unreadCount > 0) {
            badge.textContent = unreadCount > 9 ? '9+' : unreadCount.toString();
            badge.classList.remove('notification-badge--hidden');
        } else {
            badge.classList.add('notification-badge--hidden');
        }
    }

    function updateDropdown() {
        const list = document.getElementById('notification-list');
        if (!list) return;

        const seenIds = getSeenIds();

        if (notifications.length === 0) {
            list.innerHTML = '<div class="notification-empty">No new notifications</div>';
            return;
        }

        list.innerHTML = notifications.map(n => {
            const isUnread = !seenIds.has(n.id);
            const stateClass = getStateClass(n.to_state);
            const relativeTime = formatRelativeTime(n.created_datetime);
            
            return `
                <a href="/dashboard/workflow/${n.workflow_public_id}" 
                   class="notification-item ${isUnread ? 'notification-item--unread' : ''}"
                   data-notification-id="${n.id}">
                    <span class="notification-icon ${stateClass}">${getStateIcon(n.to_state)}</span>
                    <div class="notification-content">
                        <span class="notification-summary">${escapeHtml(n.summary)}</span>
                        <span class="notification-time">${relativeTime}</span>
                    </div>
                </a>
            `;
        }).join('');

        // Add click handlers to mark as read
        list.querySelectorAll('.notification-item').forEach(item => {
            item.addEventListener('click', () => {
                const id = parseInt(item.dataset.notificationId, 10);
                if (id) {
                    addSeenIds([id]);
                }
            });
        });
    }

    function getStateClass(state) {
        switch (state) {
            case 'failed': return 'notification-icon--danger';
            case 'awaiting_approval': return 'notification-icon--warning';
            case 'completed': return 'notification-icon--success';
            default: return '';
        }
    }

    function getStateIcon(state) {
        switch (state) {
            case 'failed': return '!';
            case 'awaiting_approval': return '?';
            case 'completed': return '✓';
            default: return '•';
        }
    }

    // -------------------------------------------------------------------------
    // Toast Notifications
    // -------------------------------------------------------------------------

    function showToast(notification) {
        const container = getToastContainer();
        
        const toast = document.createElement('div');
        toast.className = `notification-toast notification-toast--${getToastVariant(notification.to_state)}`;
        toast.innerHTML = `
            <span class="notification-toast-icon">${getStateIcon(notification.to_state)}</span>
            <div class="notification-toast-content">
                <span class="notification-toast-summary">${escapeHtml(notification.summary)}</span>
                <span class="notification-toast-time">${formatRelativeTime(notification.created_datetime)}</span>
            </div>
            <button class="notification-toast-close" aria-label="Dismiss">&times;</button>
        `;

        // Click to navigate
        toast.addEventListener('click', (e) => {
            if (!e.target.classList.contains('notification-toast-close')) {
                addSeenIds([notification.id]);
                window.location.href = `/dashboard/workflow/${notification.workflow_public_id}`;
            }
        });

        // Close button
        toast.querySelector('.notification-toast-close').addEventListener('click', (e) => {
            e.stopPropagation();
            removeToast(toast);
        });

        container.appendChild(toast);

        // Trigger animation
        requestAnimationFrame(() => {
            toast.classList.add('notification-toast--visible');
        });

        // Auto-dismiss
        setTimeout(() => removeToast(toast), TOAST_DURATION_MS);
    }

    function getToastContainer() {
        let container = document.getElementById('notification-toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'notification-toast-container';
            container.className = 'notification-toast-container';
            document.body.appendChild(container);
        }
        return container;
    }

    function removeToast(toast) {
        toast.classList.remove('notification-toast--visible');
        setTimeout(() => toast.remove(), 300);
    }

    function getToastVariant(state) {
        switch (state) {
            case 'failed': return 'danger';
            case 'awaiting_approval': return 'warning';
            case 'completed': return 'success';
            default: return 'info';
        }
    }

    // -------------------------------------------------------------------------
    // Dropdown Toggle
    // -------------------------------------------------------------------------

    function initDropdown() {
        const bell = document.getElementById('notification-bell');
        const dropdown = document.getElementById('notification-dropdown');
        
        if (!bell || !dropdown) return;

        bell.addEventListener('click', (e) => {
            e.stopPropagation();
            const isOpen = dropdown.classList.toggle('notification-dropdown--open');
            
            if (isOpen) {
                // Mark all as seen when dropdown is opened
                const ids = notifications.map(n => n.id);
                addSeenIds(ids);
                updateBadge();
                updateDropdown();
            }
        });

        // Close dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (!dropdown.contains(e.target) && !bell.contains(e.target)) {
                dropdown.classList.remove('notification-dropdown--open');
            }
        });

        // Close on escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                dropdown.classList.remove('notification-dropdown--open');
            }
        });
    }

    // -------------------------------------------------------------------------
    // Utility Functions
    // -------------------------------------------------------------------------

    function formatRelativeTime(isoString) {
        if (!isoString) return '';
        
        const date = new Date(isoString);
        const now = new Date();
        const diffMs = now - date;
        const diffSec = Math.floor(diffMs / 1000);
        const diffMin = Math.floor(diffSec / 60);
        const diffHour = Math.floor(diffMin / 60);
        const diffDay = Math.floor(diffHour / 24);

        if (diffSec < 60) return 'just now';
        if (diffMin < 60) return `${diffMin} min ago`;
        if (diffHour < 24) return `${diffHour} hour${diffHour > 1 ? 's' : ''} ago`;
        if (diffDay < 7) return `${diffDay} day${diffDay > 1 ? 's' : ''} ago`;
        
        return date.toLocaleDateString();
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // -------------------------------------------------------------------------
    // Initialization
    // -------------------------------------------------------------------------

    function init() {
        // Only initialize if the notification bell exists (user is logged in)
        if (!document.getElementById('notification-bell')) {
            return;
        }

        initDropdown();
        updateBadge();
        updateDropdown();

        // Start polling
        startPolling();

        // Handle visibility changes
        document.addEventListener('visibilitychange', handleVisibilityChange);
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
