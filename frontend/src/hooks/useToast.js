import { useState, useCallback } from "react";

let _nextId = 0;

/**
 * Hook for managing toast notifications.
 *
 * @returns {{ toasts, addToast, removeToast }}
 *
 * addToast({ type: "success"|"error"|"info"|"faucet", title, message, duration? })
 */
export function useToast() {
  const [toasts, setToasts] = useState([]);

  const addToast = useCallback(
    ({ type = "info", title, message, duration = 5000 }) => {
      const id = ++_nextId;
      setToasts((prev) => [...prev, { id, type, title, message, duration }]);
      return id;
    },
    [],
  );

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return { toasts, addToast, removeToast };
}
