import { useState, useEffect } from "react";
import { apiTry } from "./api";
import { useLocation } from "wouter";

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(path !== null);
  const [, navigate] = useLocation();

  useEffect(() => {
    if (!path) return;
    let mounted = true;
    setLoading(true);
    apiTry<T>(path).then((res) => {
      if (!mounted) return;
      setData(res.data);
      setError(res.error);
      setStatus(res.status);
      setLoading(false);
      if (res.status === 401) {
         navigate("/login?reason=session");
      }
    });
    return () => { mounted = false; };
  }, [path, navigate]);

  return { data, error, status, loading };
}
